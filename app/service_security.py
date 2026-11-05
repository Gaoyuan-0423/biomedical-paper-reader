"""Session boundary for the MyGo-owned loopback service."""
import secrets

from starlette.responses import PlainTextResponse, RedirectResponse


def protect_service(app, port, bootstrap_token):
    authority = f'127.0.0.1:{port}'
    origin = f'http://{authority}'
    cookie_name = f'bioreader_session_{port}'
    session_token = secrets.token_urlsafe(32)

    @app.middleware('http')
    async def owned_session(request, call_next):
        if request.headers.get('host') != authority:
            return PlainTextResponse('Forbidden', status_code=403)
        supplied_origin = request.headers.get('origin')
        fetch_site = request.headers.get('sec-fetch-site', '').lower()
        if (supplied_origin and supplied_origin != origin) or fetch_site in {'cross-site', 'same-site'}:
            return PlainTextResponse('Forbidden', status_code=403)
        bootstrap = request.query_params.get('desktop_token', '')
        if request.url.path == '/' and request.method == 'GET' and bootstrap:
            if not secrets.compare_digest(bootstrap.encode('utf-8'), bootstrap_token.encode('ascii')):
                return PlainTextResponse('Forbidden', status_code=403)
            response = RedirectResponse('/', status_code=303)
            response.set_cookie(cookie_name, session_token, httponly=True, samesite='strict', path='/')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        cookie = request.cookies.get(cookie_name, '')
        if not secrets.compare_digest(cookie.encode('utf-8'), session_token.encode('ascii')):
            return PlainTextResponse('Forbidden', status_code=403)
        response = await call_next(request)
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
