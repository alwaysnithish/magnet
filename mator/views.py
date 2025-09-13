import os
import re
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

logger = logging.getLogger('mator')

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

@csrf_protect
@require_http_methods(["GET", "POST"])
def download_view(request):
    """
    Main view for handling magnet link downloads with WebTorrent
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
            
            # For WebTorrent, we just pass the magnet link to the frontend
            context['magnet_link'] = magnet_link
            logger.info(f"Processing magnet link for WebTorrent: {magnet_link[:50]}...")
                
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
        status_info = {
            'status': 'ok',
            'service': 'WebTorrent Browser-based Downloads',
            'server_side_torrents': False,
            'browser_support': True,
        }
        return JsonResponse(status_info)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

@require_http_methods(["POST"])
def validate_magnet(request):
    """
    API endpoint to validate magnet links
    """
    try:
        magnet_link = request.POST.get("magnet", "").strip()
        
        if not magnet_link:
            return JsonResponse({
                'valid': False,
                'error': 'Empty magnet link'
            })
        
        if not is_valid_magnet(magnet_link):
            return JsonResponse({
                'valid': False,
                'error': 'Invalid magnet link format'
            })
        
        return JsonResponse({
            'valid': True,
            'message': 'Valid magnet link'
        })
        
    except Exception as e:
        logger.error(f"Error validating magnet: {e}")
        return JsonResponse({
            'valid': False,
            'error': 'Validation error'
        }, status=500)
