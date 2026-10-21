"""Query identity by default; --poll exchanges current settings without toggling output."""
import argparse
import json
import time

import drk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port')
    parser.add_argument('--poll', type=float, default=0, metavar='SECONDS')
    args = parser.parse_args()
    info = drk.open(args.port)
    try:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        settings = info['initial_settings']
        end = time.perf_counter() + args.poll
        while time.perf_counter() < end:
            data = drk.exchange(settings['voltage'], settings['current'])
            print(json.dumps({k: data[k] for k in
                             ('voltage', 'current', 'power', 'temperature', 'fault',
                              'enabled', 'sent_at_ns', 'received_at_ns')}), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        drk.close()


if __name__ == '__main__':
    main()
