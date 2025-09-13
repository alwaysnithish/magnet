import os
import re
import time
import logging
import threading
from pathlib import Path
from django.shortcuts import render
from django.http import FileResponse, HttpResponse, JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.core.files.storage import default_storage
from django.utils.decorators import method_decorator

logger = logging.getLogger('mator')

class TorrentDownloadError(Exception):
    """Custom exception for torrent download errors"""
    pass

def is_valid_magnet(magnet):
    """
    Validate magnet link format
    """
    if not magnet or not isinstance(magnet, str):
        return False
    
    # Basic magnet link validation
    if not magnet.startswith("magnet:?xt=urn:btih:"):
        return False
    
    if len(magnet) < 50:  # Minimum reasonable length
        return False
    
    # Check for required hash
    if not re.search(r'xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet):
        return False
    
    return True

def cleanup_old_files(directory, max_age_hours=1):
    """
    Clean up old downloaded files
    """
    try:
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getctime(filepath)
                if file_age > max_age_seconds:
                    os.remove(filepath)
                    logger.info(f"Cleaned up old file: {filename}")
    except Exception as e:
        logger.warning(f"Error cleaning up files: {e}")

def download_magnet_file(magnet_link):
    """
    Download file from magnet link using libtorrent
    """
    try:
        import libtorrent as lt
    except ImportError as e:
        logger.error(f"LibTorrent import failed: {e}")
        raise TorrentDownloadError("LibTorrent library is not available. Please contact administrator.")

    try:
        # Clean up old files first
        cleanup_old_files(settings.DOWNLOADS_DIR)
        
        # Ensure downloads directory exists with proper permissions
        os.makedirs(settings.DOWNLOADS_DIR, exist_ok=True)
        
        logger.info(f"Starting download for magnet: {magnet_link[:50]}...")
        
        # Create session with proper settings
        ses = lt.session()
        ses.set_alert_mask(
            lt.alert.category_t.error_notification | 
            lt.alert.category_t.status_notification |
            lt.alert.category_t.storage_notification
        )
        
        # Configure session settings
        settings_dict = {
            'user_agent': 'MagnetDownloader/1.0',
            'listen_interfaces': '0.0.0.0:6881',
            'enable_dht': True,
            'enable_lsd': True,
            'enable_upnp': True,
            'enable_natpmp': True,
        }
        ses.apply_settings(settings_dict)
        
        # Add magnet URI
        params = {
            'save_path': str(settings.DOWNLOADS_DIR),
            'storage_mode': lt.storage_mode_t.storage_mode_sparse,
        }
        
        handle = lt.add_magnet_uri(ses, magnet_link, params)
        
        if not handle.is_valid():
            raise TorrentDownloadError("Invalid magnet link provided.")

        logger.info("Downloading metadata...")
        
        # Wait for metadata with timeout
        metadata_timeout = getattr(settings, 'TORRENT_METADATA_TIMEOUT', 60)
        start_time = time.time()
        
        while not handle.has_metadata():
            if time.time() - start_time > metadata_timeout:
                raise TorrentDownloadError(
                    "Timeout while downloading metadata. "
                    "The magnet link might be invalid or have no active seeders."
                )
            
            # Check for errors
            alerts = ses.pop_alerts()
            for alert in alerts:
                if alert.category() & lt.alert.category_t.error_notification:
                    error_msg = alert.message()
                    logger.error(f"Torrent error during metadata: {error_msg}")
                    raise TorrentDownloadError(f"Torrent error: {error_msg}")
            
            status = handle.status()
            logger.debug(f"Metadata progress: {status.progress * 100:.1f}%")
            time.sleep(1)

        logger.info("Metadata acquired successfully")
        
        # Get torrent info and validate
        torrent_info = handle.get_torrent_info()
        
        if torrent_info.num_files() == 0:
            raise TorrentDownloadError("No files found in torrent.")
        
        # Check file size limits
        total_size = torrent_info.total_size()
        max_size = getattr(settings, 'MAX_DOWNLOAD_SIZE', 500 * 1024 * 1024)  # 500MB default
        
        if total_size > max_size:
            raise TorrentDownloadError(
                f"File too large ({total_size // (1024*1024)}MB). "
                f"Maximum allowed size is {max_size // (1024*1024)}MB."
            )
        
        # Get file information
        files = torrent_info.files()
        file_info = files.file_path(0)  # Get first file
        
        # Sanitize filename
        filename = os.path.basename(file_info)
        if not filename or filename in ['.', '..']:
            filename = f"downloaded_file_{int(time.time())}"
        
        # Remove unsafe characters
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        
        logger.info(f"Starting download: {filename} ({total_size // 1024} KB)")
        
        # Full paths
        expected_full_path = os.path.join(settings.DOWNLOADS_DIR, file_info)
        sanitized_path = os.path.join(settings.DOWNLOADS_DIR, filename)
        
        # Start actual download
        download_timeout = getattr(settings, 'TORRENT_DOWNLOAD_TIMEOUT', 300)
        start_download_time = time.time()
        last_progress = 0
        stalled_time = 0
        
        while not handle.status().is_finished:
            current_time = time.time()
            elapsed_time = current_time - start_download_time
            
            # Check overall timeout
            if elapsed_time > download_timeout:
                raise TorrentDownloadError(
                    f"Download timeout ({download_timeout}s). "
                    "File may be too large or insufficient seeders available."
                )
            
            # Check for errors
            alerts = ses.pop_alerts()
            for alert in alerts:
                if alert.category() & lt.alert.category_t.error_notification:
                    error_msg = alert.message()
                    logger.error(f"Download error: {error_msg}")
                    raise TorrentDownloadError(f"Download error: {error_msg}")
            
            status = handle.status()
            current_progress = status.progress
            
            # Check for stalled downloads
            if abs(current_progress - last_progress) < 0.001:  # Less than 0.1% progress
                stalled_time += 2
                if stalled_time > 30:  # 30 seconds without progress
                    raise TorrentDownloadError("Download appears to be stalled. No progress for 30 seconds.")
            else:
                stalled_time = 0
                last_progress = current_progress
            
            logger.info(
                f"Progress: {current_progress * 100:.1f}% | "
                f"Speed: {status.download_rate / 1024:.1f} KB/s | "
                f"Peers: {status.num_peers} | "
                f"Seeds: {status.num_seeds}"
            )
            
            # Check if we have enough data (95% is often sufficient)
            if current_progress >= 0.95:
                potential_paths = [expected_full_path, sanitized_path]
                for path in potential_paths:
                    if os.path.exists(path) and os.path.getsize(path) > 0:
                        logger.info(f"Download appears complete at 95%, file found: {path}")
                        return path, None
            
            time.sleep(2)

        # Download completed, find the file
        potential_paths = [
            expected_full_path,
            sanitized_path,
            os.path.join(settings.DOWNLOADS_DIR, torrent_info.name()),
        ]
        
        # Also check all files in download directory
        try:
            for item in os.listdir(settings.DOWNLOADS_DIR):
                item_path = os.path.join(settings.DOWNLOADS_DIR, item)
                if os.path.isfile(item_path):
                    potential_paths.append(item_path)
        except Exception as e:
            logger.warning(f"Could not list download directory: {e}")
        
        # Find the downloaded file
        for path in potential_paths:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                logger.info(f"Download completed successfully: {path}")
                return path, None
        
        # File not found - log directory contents for debugging
        try:
            dir_contents = os.listdir(settings.DOWNLOADS_DIR)
            logger.error(f"File not found. Directory contents: {dir_contents}")
        except Exception as e:
            logger.error(f"Cannot list directory: {e}")
        
        raise TorrentDownloadError(
            "Download completed but file not found in expected location. "
            "This might be a temporary filesystem issue."
        )
        
    except TorrentDownloadError:
        raise  # Re-raise our custom errors
    except Exception as e:
        logger.error(f"Unexpected error in download_magnet_file: {e}")
        raise TorrentDownloadError(f"Unexpected download error: {str(e)}")

@csrf_protect
@require_http_methods(["GET", "POST"])
def download_view(request):
    """
    Main view for handling magnet link downloads
    """
    context = {'error': None}
    
    if request.method == "POST":
        try:
            magnet_link = request.POST.get("magnet", "").strip()
            
            # Validate input
            if not magnet_link:
                context['error'] = "Please provide a magnet link."
                logger.warning("Empty magnet link submitted")
                return render(request, "download.html", context)
            
            if not is_valid_magnet(magnet_link):
                context['error'] = (
                    "Invalid magnet link format. Please ensure it starts with "
                    "'magnet:?xt=urn:btih:' and contains a valid hash."
                )
                logger.warning(f"Invalid magnet format submitted: {magnet_link[:50]}...")
                return render(request, "download.html", context)
            
            # Log the download attempt
            logger.info(f"Starting download process for magnet: {magnet_link[:50]}...")
            
            # Download the file
            file_path, error_msg = download_magnet_file(magnet_link)
            
            if file_path and os.path.exists(file_path):
                try:
                    filename = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    logger.info(f"Serving file: {filename} ({file_size} bytes)")
                    
                    # Create file response
                    response = FileResponse(
                        open(file_path, "rb"),
                        as_attachment=True,
                        filename=filename
                    )
                    
                    # Add headers for better download experience
                    response['Content-Length'] = file_size
                    response['Content-Type'] = 'application/octet-stream'
                    
                    return response
                    
                except Exception as e:
                    logger.error(f"Error serving file: {e}")
                    context['error'] = f"Error preparing file for download: {str(e)}"
            else:
                context['error'] = error_msg or "Download failed for unknown reason."
                logger.error(f"Download failed: {context['error']}")
                
        except TorrentDownloadError as e:
            context['error'] = str(e)
            logger.error(f"Torrent download error: {e}")
        except Exception as e:
            context['error'] = "An unexpected error occurred. Please try again later."
            logger.error(f"Unexpected error in download_view: {e}")
    
    return render(request, "download.html", context)

@require_http_methods(["GET"])
def status_view(request):
    """
    Simple status check view
    """
    try:
        import libtorrent as lt
        status_info = {
            'status': 'ok',
            'libtorrent_version': lt.version,
            'downloads_dir': str(settings.DOWNLOADS_DIR),
            'downloads_dir_exists': os.path.exists(settings.DOWNLOADS_DIR),
            'downloads_dir_writable': os.access(settings.DOWNLOADS_DIR, os.W_OK),
        }
        return JsonResponse(status_info)
    except ImportError:
        return JsonResponse({
            'status': 'error',
            'message': 'LibTorrent not available'
        }, status=500)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)
