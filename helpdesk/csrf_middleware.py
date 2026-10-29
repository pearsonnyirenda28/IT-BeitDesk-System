# helpdesk/csrf_middleware.py

class SpoofNullOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # If the browser/iframe sends Origin: null, replace it with the current host
        if request.META.get('HTTP_ORIGIN') == 'null':
            scheme = 'https' if request.is_secure() else 'http'
            request.META['HTTP_ORIGIN'] = f"{scheme}://{request.get_host()}"
            
        return self.get_response(request)