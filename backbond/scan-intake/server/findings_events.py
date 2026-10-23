"""Read-only wake hints. Every transition still rechecks durable native evidence."""
import json
import re
from urllib.parse import urlsplit


class NativeEvents:
    def __init__(self, gateway, company, changed, *, connect=None):
        self.gateway, self.company, self.changed = gateway, company, changed
        self.connect = connect
        self.connected = False

    def run(self, stopped):
        url = urlsplit(self.gateway.upstream)
        endpoint = ('wss' if url.scheme == 'https' else 'ws') + '://' + url.netloc
        endpoint += '/api/companies/' + self.company + '/events/ws'
        while not stopped.is_set():
            try:
                connect = self.connect
                if connect is None:
                    from websockets.sync.client import connect
                with connect(endpoint, additional_headers={'Authorization': 'Bearer ' + self.gateway.upstream_token},
                             proxy=None, open_timeout=10, close_timeout=1, max_size=2_000_000) as socket:
                    self.connected = True
                    self.changed(None)  # Reconcile after reconnect; the stream is not a durable ledger.
                    while not stopped.is_set():
                        try:
                            event = json.loads(socket.recv(timeout=1))
                        except TimeoutError:
                            continue
                        except (ValueError, TypeError):
                            continue
                        if (isinstance(event, dict) and event.get('companyId') == self.company
                                and event.get('type') == 'activity.logged'
                                and isinstance(event.get('payload'), dict)
                                and event['payload'].get('entityType') == 'issue'):
                            self.changed(event)
            except Exception:
                pass  # No event bodies, credentials or exception text in logs.
            finally:
                self.connected = False
            stopped.wait(3)


class IntakeEvents:
    def __init__(self, base_url, token, http, changed):
        self.base_url, self.token, self.http, self.changed = base_url, token, http, changed
        self.connected = False

    def run(self, stopped):
        url = urlsplit(self.base_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or not self.token):
            return
        cursor = ''
        while not stopped.is_set():
            try:
                response = self.http.get(self.base_url.rstrip('/') + '/findings/v1/workflows/changes',
                    params={'cursor': cursor}, headers={'Authorization': 'Bearer ' + self.token},
                    timeout=30, follow_redirects=False)
                value = response.json()
                if (response.status_code != 200 or not isinstance(value, dict)
                        or value.get('schema_version') != 'findings_changes@1'
                        or not isinstance(value.get('cursor'), str)
                        or not re.fullmatch(r'[a-f0-9]{32}:[0-9]{1,20}', value['cursor'])):
                    raise ValueError('unconfirmed notification')
                self.connected = True
                if value['cursor'] != cursor:
                    cursor = value['cursor']
                    self.changed()
            except Exception:
                self.connected = False
                cursor = ''  # A restart or lost reply requires a durable-state reconciliation.
                stopped.wait(3)
