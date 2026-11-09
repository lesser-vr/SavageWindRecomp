#!/usr/bin/env python3
"""Exercise a local Windows SBS build with a private copy of a BOTW 1.9.0 save."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time

import swctl


CONTINUE = ','.join(f'{frame}:A:4' for frame in range(300, 541, 40))
COMBAT = ('1185:ZL:380,1210:STICK_UP:34,1240:Y:4,1270:Y:4,1300:Y:4,1330:Y:4,'
          '1380:STICK_RIGHT:28,1415:Y:4,1440:Y:4,1465:Y:4,1490:Y:4,1520:Y:4,1560:Y:4,'
          '1580:RSTICK_RIGHT:160,1620:STICK_UP:170,1810:RSTICK_LEFT:170,1890:STICK_UP:220')
MAP_STICKS = [(-1, .43, 25), (-1, .43, 42), (-1, .70, 17), (-1, .17, 9)]
FRAME = re.compile(r'^\[ft\] ([\d.]+) (\d+) (\d+)$', re.M)
ATLAS = 'captured native Toy-Con eye atlas'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def frame_stats(frames, first, last):
    missing = next((i for i in range(first - 1, last + 1) if i not in frames), None)
    require(missing is None, f'Missing frame {missing} in benchmark log')
    dt = sorted(frames[i] - frames[i-1] for i in range(first, last+1))
    require(dt[0] >= 0, 'Frame clock ran backwards')
    return dict(frames=len(dt), median_ms=round(statistics.median(dt), 3),
                p99_ms=round(dt[math.ceil(.99*len(dt))-1], 3), max_ms=round(dt[-1], 3),
                over_33_ms=sum(t > 33 for t in dt), over_50_ms=sum(t > 50 for t in dt))


def check_images(case, mode, events, warp):
    import cv2
    import numpy as np

    width, height = (2560, 720) if mode == 'full' else (2560, 1440)
    shots = {int(p.stem.split('_')[-1]): p for p in (case/'build/frames').glob('window_*.png')}

    def eyes(frame, flags=cv2.IMREAD_GRAYSCALE):
        # imdecode also accepts Windows paths with non-ASCII characters.
        data = np.fromfile(shots[frame], dtype=np.uint8)
        im = cv2.imdecode(data, flags) if data.size else None
        require(im is not None and im.shape[:2] == (height, width), f'Unexpected window capture: {shots[frame]}')
        return np.hsplit(im, 2)

    def loading_page(frame):
        left, right = eyes(frame, cv2.IMREAD_COLOR)
        if not np.array_equal(left, right):
            return False
        left = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
        h, w = left.shape
        x0 = int(.72*w)
        ys, xs = np.where(left[h//2:, x0:] > 7)
        stripe = np.flatnonzero(left[:, w//100] > 220)
        if not len(xs) or not len(stripe):
            return False
        bounds = [(xs.min()+x0)/w, (xs.max()+1+x0)/w, (ys.min()+h//2)/h, (ys.max()+1+h//2)/h]
        return (np.max(np.abs(np.subtract(bounds, [.789, .962, .717, .952]))) < .006
                and stripe[0]/h < .002 and abs((stripe[-1]+1)/h - .382) < .005)

    field = sorted(n for n in shots if events[ATLAS]+30 < n <= 1199)
    require(field, 'No gameplay capture after goggles activation and before map input')
    left, right = eyes(field[-1])
    sift = cv2.SIFT_create(nfeatures=1800)
    (kl, dl), (kr, dr) = [sift.detectAndCompute(cv2.resize(a, (960, 540)), None) for a in (left, right)]
    require(dl is not None and dr is not None and len(dr) >= 2, 'Too few features in the gameplay capture')
    disparity = []
    for pair in cv2.BFMatcher().knnMatch(dl, dr, k=2):
        if len(pair) == 2 and pair[0].distance < .65 * pair[1].distance:
            m = pair[0]
            x, y = kl[m.queryIdx].pt
            u, v = kr[m.trainIdx].pt
            if abs(y-v) < 1.2:
                disparity.append(x-u)
    require(len(disparity) >= 50, 'Too few corresponding points along the same scanline')
    lo, hi = np.percentile(disparity, [10, 90])
    require(hi-lo > 1, 'Gameplay capture lacks depth-dependent horizontal disparity')
    result = dict(field_frame=field[-1], matched_points=len(disparity),
                  disparity_p10_p90_px=[round(float(lo), 2), round(float(hi), 2)])
    if warp:
        for event in ('Demo005_0', 'Demo005_1'):
            require(event in events, f'Missing fast-travel event {event}; check the map route for this save')
        usable = [n for n in sorted(shots) if events['Demo005_0'] < n <= events['Demo005_1'] and loading_page(n)]
        require(len(usable) >= 2, 'Did not capture two complete, equally framed loading pages during travel')
        result['warp_loading_frames'] = usable
    return result


def command(port, *words):
    with swctl.connect('127.0.0.1', port, 5, 2) as conn:
        line = swctl.request_line([str(w) for w in words])
        reply = swctl.parse_reply(swctl.exchange(conn, line, 5), words[0])
        require(reply['ok'], f'Game rejected {words}: {reply.get("error")}')
        return reply['data']


def drive_map(port, sticks):
    command(port, 'press', 'minus', 4)
    time.sleep(1.3)
    for x, y, frames in sticks:
        command(port, 'stick', 'l', x, y, int(frames))
        time.sleep(frames/60 + .45)
    command(port, 'press', 'a', 4)
    time.sleep(.8)
    command(port, 'press', 'a', 4)


class GameExited(Exception):
    pass


def wait_for(proc, log, marker, timeout, offset=0):
    deadline = time.monotonic() + timeout
    with log.open('rb') as stream:
        stream.seek(offset)
        tail = b''
        while time.monotonic() < deadline:
            chunk = tail + stream.read()
            if marker in chunk:
                return
            if proc.poll() is not None:
                raise GameExited
            tail = chunk[-len(marker):]
            time.sleep(.15)
    raise ValueError(f'Timed out waiting for {marker.decode()!r}; see {log}')


def check_controls(proc, log, port, sticks, full):
    wait_for(proc, log, f'listening on 127.0.0.1:{port}'.encode(), 120)
    wait_for(proc, log, b' 1199 60', 150)
    require(ATLAS in log.read_text(encoding='utf-8', errors='replace'),
            'Goggles did not activate before the field check; verify the save')
    if sticks:
        drive_map(port, sticks)
    if full:
        if sticks:
            wait_for(proc, log, b'[event] Demo005_1', 75)
            wait_for(proc, log, b' 2450 60', 60)
        command(port, 'set', 'render_scale', 50)
        for setting, value, marker in (
                ('sbs_render_scale', '2x', b'native eye depth target 1280x1440'),
                ('sbs_render_scale', 'auto', b'native eye depth target 1463x1646'),
                ('stereo_sbs', 'off', b'SBS requested Toy-Con VR goggles off')):
            offset = log.stat().st_size
            command(port, 'set', setting, value)
            wait_for(proc, log, marker, 15, offset)
        for setting, expected in (('stereo_sbs', 'off'), ('render_scale', '50'), ('sbs_render_scale', 'auto')):
            require(command(port, 'get', setting).get(setting) == expected, f'Unexpected value for {setting}')
        command(port, 'exit')


def launch(exe, game, case, env, sticks, full):
    log = case/'game.log'
    # MSVCRT exec can detach a replacement process on an address collision. Keep
    # retries here so that the process we wait for (and kill on timeout) owns the run.
    env['SAVAGEWIND_REEXEC'] = '20'
    for _ in range(20):
        with log.open('wb') as stream:
            proc = subprocess.Popen([str(exe), '--play', str(game)], cwd=case, env=env,
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            controls_complete = False
            try:
                if sticks or full:
                    check_controls(proc, log, int(env['SAVAGEWIND_CONTROL']), sticks, full)
                controls_complete = True
                proc.wait(timeout=180)
            except GameExited:
                pass  # Below, retry address collisions and fail any other premature exit.
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        text = log.read_text(encoding='utf-8', errors='replace')
        if proc.returncode and 'FATAL: guest address range' in text:
            continue
        require(proc.returncode == 0 and 'FATAL:' not in text and '*** exception' not in text,
                f'Game stopped (exit {proc.returncode}); see {log}')
        require(controls_complete, f'Game exited before control checks finished; see {log}')
        return text
    raise ValueError(f'Could not reserve the guest address range; see {log}')


def run_case(case, install, save, settings, mode, warp, sticks):
    bench = mode == 'benchmark'
    size = '2560x720' if mode == 'full' else '2560x1440'
    case.mkdir()
    (case/'build/frames').mkdir(parents=True)
    # Preserve unrelated settings in a private INI. Both sides of an A/B use this same template.
    overrides = dict(fullscreen='0', window_size=size, frame_rate='60', vsync='fast', volume='0',
                     stereo_sbs='full' if mode == 'full' else 'half', sbs_render_scale='1x',
                     render_scale='100', upscaler='off', frame_generation='0', perf_overlay='off',
                     hdr='off', save_backups='0', start_at_continue='0')
    if not bench:
        overrides.update(colour_exposure='0', colour_contrast='0', colour_saturation='0',
                         colour_temperature='0', colour_tint='0', colour_lut='none')
    ini = [line for line in settings.splitlines() if line.partition('=')[0].strip() not in overrides]
    (case/'savagewind.ini').write_text('\n'.join(ini+[f'{k} = {v}' for k, v in overrides.items()])+'\n', encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='inputs-', dir=case) as tmp:
        scratch = Path(tmp)
        shutil.copytree(save, scratch/'save/save_0')
        cache = scratch/'cache'
        cache.mkdir()
        seed = install/'cache'
        if seed.is_dir():
            for p in seed.iterdir():
                if p.is_file():
                    shutil.copyfile(p, cache/p.name)
                elif p.name in ('spirv', 'xlate'):
                    shutil.copytree(p, cache/p.name)
        env = {k:v for k, v in os.environ.items() if not k.upper().startswith('SAVAGEWIND_')}
        end = 2160 if bench else 2650 if warp else 1250
        env.update(SAVAGEWIND_DATA_ROOT=str(case), SAVAGEWIND_SAVE_DIR=str(scratch/'save'),
                   SAVAGEWIND_SETTINGS=str(case/'savagewind.ini'), SAVAGEWIND_CACHE_DIR=str(cache),
                   SAVAGEWIND_NO_LAUNCHER='1', SAVAGEWIND_HIDDEN='1', SAVAGEWIND_WINDOW=size, SAVAGEWIND_FRAME_LOG='1',
                   SAVAGEWIND_TEST_WARP='3090.5,165.2,2169.7' if bench else '2939.06,115.44,2157.93',
                   SAVAGEWIND_TEST_WEATHER='0', SAVAGEWIND_TEST_TIME='15' if bench else '18.52',
                   SAVAGEWIND_EXIT_AT=str(4500 if mode == 'full' else end),
                   SAVAGEWIND_INPUT=CONTINUE+(','+COMBAT if bench else ''))
        if not bench:
            shots = [1000, 1100, 1150]
            if warp:
                shots.extend(range(1450, end, 35))
            if warp or mode == 'full':
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    env['SAVAGEWIND_CONTROL'] = str(sock.getsockname()[1])
            env['SAVAGEWIND_WINDOW_SHOT_AT'] = ','.join(map(str, shots))
        text = launch(install/'savagewind.exe', install/'extracted', case, env, sticks if warp else None, mode == 'full')

    version = re.search(r'^\[gamelib\] .+\(game version ([^,)]+)', text, re.M)
    require(version and version[1] == '1.9.0', 'Expected the BOTW 1.9.0 game library')
    require(f'swapchain {size}' in text, f'Expected window size {size}')
    require(ATLAS in text and 'native eye depth target' in text, 'No native Toy-Con views; check the build and save')
    samples = FRAME.findall(text)
    last_required = (2450 if warp else 1199) if mode == 'full' else end-1
    require(samples and int(samples[-1][1]) >= last_required, 'Game exited before the scripted route ended')
    frames = {int(i):float(t) for t, i, fps in samples if fps == '60'}
    result = dict(build=next((s.partition(' ')[2] for s in text.splitlines() if s.startswith('[savagewind] ')), 'unreported'),
                  game_version=version[1], window=size, target_fps=60,
                  gpu=next((s for s in text.splitlines() if s.startswith('[vk] using ')), 'unreported'),
                  driver=next((s for s in text.splitlines() if s.startswith('[vk] driver:')), 'unreported'))
    if bench:
        start = re.search(r'^\[ft\] [\d.]+ 1180 60$', text, re.M)
        require(start and ATLAS in text[:start.start()], 'Benchmark entered combat before native SBS started')
        require(re.search(r'\[place\].*x=3090\.50 .*z=2169\.70', text), 'Benchmark did not reach its start position')
        result.update(first_load=frame_stats(frames,600,1179), combat_walk=frame_stats(frames,1180,2159))
    else:
        field_start = re.search(r'^\[ft\] [\d.]+ 1199 60$', text, re.M)
        depth_1x = '640x720' if mode == 'full' else '1280x1440'
        require(field_start and f'native eye depth target {depth_1x} ' in text[:field_start.start()],
                f'Expected initial 1x native eye target {depth_1x}')
        require('SBS requested Toy-Con VR goggles on' in text[:field_start.start()],
                'Automatic goggles activation was not observed; use a field save with goggles initially off')
        events = {}
        frame = 0
        for line in text.splitlines():
            m = FRAME.match(line)
            if m:
                frame = int(m[2])
            for token in (ATLAS, 'Demo005_0', 'Demo005_1'):
                if token in line:
                    events.setdefault(token, frame)
        result.update(check_images(case, mode, events, warp))
        result.update(eye_target_1x=depth_1x, goggles_requested_on=True)
        if mode == 'full':
            result.update(live_eye_targets={'2x': '1280x1440', 'auto': '1463x1646'},
                          goggles_requested_off=True, regular_render_scale_in_2d='50')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True, type=Path, help='installed SBS build containing savagewind.exe and extracted/')
    parser.add_argument('--save', required=True, type=Path, help='save directory containing save_0, or the save_0 directory itself')
    parser.add_argument('--out', required=True, type=Path, help='new directory for logs, captures and results.json')
    parser.add_argument('--suite', choices=('all', 'stereo', 'benchmark'), default='all',
                        help='all: Half/Full including map travel, then benchmark; stereo: Half/Full field only')
    parser.add_argument('--baseline', type=Path, help='also benchmark this SBS installation with the candidate settings')
    parser.add_argument('--repeat', type=int, default=1, help='benchmark runs per build')
    parser.add_argument('--map-stick', action='append', type=float, nargs=3, metavar=('X','Y','FRAMES'),
                        help='override a map cursor movement; repeat for all steps from the fixed start position')
    args = parser.parse_args()
    install, save_root, out = (p.resolve() for p in (args.install, args.save, args.out))
    save = (save_root/'save_0').resolve() if (save_root/'save_0').is_dir() else save_root
    baseline = args.baseline.resolve() if args.baseline else None
    builds = [p for p in (baseline, install) if p]
    require(os.name == 'nt', 'This runner supports Windows Vulkan builds')
    require(args.repeat > 0, '--repeat must be positive')
    require(not baseline or args.suite != 'stereo', '--baseline requires the benchmark')
    require(args.repeat == 1 or args.suite != 'stereo', '--repeat requires the benchmark')
    require(not args.map_stick or args.suite == 'all', '--map-stick requires the fast-travel check (--suite all)')
    sticks = args.map_stick or MAP_STICKS
    require(all(-1 <= x <= 1 and -1 <= y <= 1 and 1 <= n <= 600 and int(n) == n for x, y, n in sticks),
            'Map stick values must be in [-1, 1] with 1 to 600 whole frames')
    require(any(save.glob('[0-9]*/game_data.sav')), 'Expected a save_0 slot with a numbered gameplay save')
    for p in builds:
        require((p/'savagewind.exe').is_file() and (p/'extracted/swgame.dll').is_file(), f'Incomplete SBS installation: {p}')
    inputs = [save_root, save, *builds]
    inputs.extend((p/sub).resolve() for p in builds for sub in ('cache', 'extracted', 'save'))
    require(not any(out == p or p in out.parents for p in inputs), 'Choose an output directory outside the input folders')
    if args.suite != 'benchmark':
        try:
            import cv2
            cv2.SIFT_create()
        except (ImportError, AttributeError) as e:
            raise ValueError('Stereo checks require opencv-python with SIFT; see docs/SBS-testing.md') from e
    cfg = install/'savagewind.ini'
    settings = cfg.read_text(encoding='utf-8-sig') if cfg.exists() else ''
    out.mkdir(parents=True, exist_ok=False)
    cases = []
    if args.suite != 'benchmark':
        cases.extend((m, install, m) for m in ('half', 'full'))
    if args.suite != 'stereo':
        order = ([('baseline',baseline)] if baseline else []) + [('candidate',install)]
        for i in range(args.repeat):
            for name, p in (order if i%2 == 0 else reversed(order)):
                cases.append((f'{name}-{i+1}', p, 'benchmark'))
    report = {}
    for name, p, mode in cases:
        print(f'Running {name}: {out/name}', flush=True)
        try:
            data = run_case(out/name, p, save, settings, mode, args.suite == 'all' and mode != 'benchmark', sticks)
            report[name] = dict(status='passed', **data)
        except Exception as e:
            report[name] = dict(status='failed', error=str(e))
            raise
        finally:
            (out/'results.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(data), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f'SBS check failed: {error}')
