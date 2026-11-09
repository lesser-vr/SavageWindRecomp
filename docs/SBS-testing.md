# Testing a Windows SBS build

Run `tools/test_sbs.py` from a checkout of this fork after updating the renderer or merging upstream changes. It runs an already-built game on your local Vulkan GPU. It does not need a physical 3D monitor.

Use a working SBS installation with your own BOTW **1.9.0** game (`extracted/swgame.dll` already built). Use a field save that **Continue** can load, with the Toy-Con VR goggles initially **off** so the test can observe the game switching them on. Close the game before testing. For image checks, install OpenCV and NumPy in your Python 3 environment:

```powershell
py -m pip install opencv-python numpy
py tools/test_sbs.py --install 'D:\Games\SavageWind-SBS' --save 'D:\TestSaves\BOTW\save_0' --out 'D:\TestResults\sbs-01'
```

`--save` accepts either the folder containing `save_0` or `save_0` itself. The runner gives each game process a temporary copy of the save and shader caches and a separate settings file. An installation whose save folder is a junction can be used the same way. Choose a **new** output folder outside the installs, saves and caches. The runner keeps the log, private settings, screenshots and `results.json` there; it removes the temporary saves and caches when each case finishes. Keep captures and logs local unless you have reviewed them for sharing.

| Suite | What it checks |
| --- | --- |
| `all` (default) | Tests Half-SBS at 2560×1440 and Full-SBS at 2560×720. Checks automatic goggles activation and depth-dependent horizontal disparity on actual presented frames. Fast-travels in each mode and checks that the loading logo and banner have the expected framing and that both eyes match. In Full-SBS it also tests live scale changes and turning SBS off. Finishes with a separate frame-time benchmark. |
| `stereo` | Same field, scale and goggles checks; skips fast travel and the benchmark. Use this when the save has no suitable travel point. |
| `benchmark` | Runs just the fixed 60 fps Half-SBS route, with no screenshots. Requires only Python's standard library and the included `swctl.py`. |

The visual checks use SDR and 1x SBS scale initially. In Full-SBS the runner sets the regular render scale to 50%, then changes the SBS scale to 2x and Auto. It checks the actual GPU eye depth targets (1280×1440 and 1463×1646 for this output), switches SBS off, and verifies that the game requests goggles-off and still reports 50% for 2D and Auto for SBS. These sizes are render targets before the goggles crop; they are not the final panel resolution.

The default map route starts near (2939, 115, 2158) and expects an unlocked travel point near (−966, 128, 725). The cursor movements depend on the save's map state. For another destination, pass a sequence of `--map-stick X Y FRAMES` options to replace the default movements (`X` and `Y` range from −1 to 1). A missing travel event is a failed route; inspect the captures or try `--suite stereo` to test field rendering separately.

To compare a candidate against a previously working **SBS** build:

```powershell
py tools/test_sbs.py --install 'D:\Games\SavageWind-SBS' --baseline 'D:\Games\SavageWind-SBS-previous' --save 'D:\TestSaves\BOTW\save_0' --suite benchmark --repeat 2 --out 'D:\TestResults\perf-01'
```

The report records each build and GPU, then the median, p99, maximum, and counts above 33 and 50 ms for two windows: initial load (580 frames) and combat/movement (980 frames). Both builds use the candidate's settings and the same save and inputs; each starts with its own installed cache copied afresh. Repeats alternate which build runs first. Compare under similar GPU load and cache conditions. A benchmark pass means the route and frame log were complete; there is no universal frame-time pass threshold.

Exit code 0 means all selected cases passed. On failure the runner stops at the first failing case; check its entry in `results.json` and its `game.log`. Use the saved field and travel images to investigate visual failures. This checks the game's window output; monitor SBS decoding, F11 display switching, and perceived comfort still need a display test.
