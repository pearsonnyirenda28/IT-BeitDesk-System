#!/usr/bin/env python
"""
BeitDesk — Daphne ASGI server
Handles both HTTP and WebSocket (real-time notifications) connections.

HTTP:      http://<your-ip>:8000
HTTPS/WSS: https://<your-ip>:8000  (via run_https.py)

Usage:
    python run_daphne.py            # HTTP on port 8000
    python run_daphne.py --port 9000
    python run_daphne.py --https    # HTTPS with self-signed cert (LAN)

WebSocket connects automatically at ws://<host>/ws/notifications/
"""
import os
import sys
import argparse

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nexus_helpdesk.settings')

def main():
    parser = argparse.ArgumentParser(description='BeitDesk Daphne ASGI server')
    parser.add_argument('--host',  default='0.0.0.0', help='Bind host (default 0.0.0.0)')
    parser.add_argument('--port',  default=8000, type=int, help='Port (default 8000)')
    parser.add_argument('--https', action='store_true', help='Enable HTTPS/WSS via self-signed cert')
    args = parser.parse_args()

    if args.https:
        # Use werkzeug SSL for HTTPS — needed for camera (MFA) on LAN devices
        from run_https import main as run_https_main
        run_https_main()
        return

    from daphne.cli import CommandLineInterface
    sys.argv = [
        'daphne',
        '-b', args.host,
        '-p', str(args.port),
        '--access-log', '-',
        'nexus_helpdesk.asgi:application',
    ]
    CommandLineInterface.entrypoint()

if __name__ == '__main__':
    main()
