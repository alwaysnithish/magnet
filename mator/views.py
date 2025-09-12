import os
import re
import time
from django.shortcuts import render
from django.http import FileResponse, HttpResponse, Http404
from django.conf import settings

def is_valid_magnet(magnet):
    return magnet.startswith("magnet:?xt=urn:btih:") and len(magnet) > 40

def download_magnet_file(magnet_link):
    import libtorrent as lt

    ses = lt.session()
    params = {
        'save_path': str(settings.DOWNLOADS_DIR),
        'storage_mode': lt.storage_mode_t(2),
    }
    try:
        handle = lt.add_magnet_uri(ses, magnet_link, params)
    except Exception as e:
        print(f"libtorrent error: {e}")
        return None, "Invalid magnet link or libtorrent error."

    print("Downloading metadata...")
    while not handle.has_metadata():
        s = handle.status()
        print(f"Progress: {s.progress * 100:.2f}%, Downloading metadata...")
        time.sleep(1)

    print("Metadata acquired. Starting download...")
    torrent_info = handle.get_torrent_info()
    filename = torrent_info.files().file_path(0)
    download_path = os.path.join(settings.DOWNLOADS_DIR, filename)

    while handle.status().state != lt.torrent_status.seeding:
        s = handle.status()
        print(f"Progress: {s.progress * 100:.2f}%")
        time.sleep(2)

    print("Download complete:", download_path)
    if os.path.exists(download_path):
        return download_path, None
    else:
        return None, "File not found after download."

def download_view(request):
    error = None
    file_url = None
    if request.method == "POST":
        magnet_link = request.POST.get("magnet")
        if not is_valid_magnet(magnet_link):
            error = "Invalid magnet link. Please check and try again."
        else:
            file_path, error_msg = download_magnet_file(magnet_link)
            if file_path:
                filename = os.path.basename(file_path)
                response = FileResponse(open(file_path, "rb"), as_attachment=True, filename=filename)
                return response
            else:
                error = error_msg or "Unknown error occurred during download."
    return render(request, "download.html", {"error": error})