"""Check HUD-only VR query patches with synthetic guest instructions."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import gen
import patches

ROOT = Path(__file__).resolve().parent.parent
BASE, GETTER = 0x7000000000, 0x7000000040
RET, NOP = 0xd65f03c0, 0xd503201f

for locals_ in (None, gen.Locals()):
    for linked in (False, True):
        branch = (0x94000000 if linked else 0x14000000) | ((GETTER - BASE) // 4)
        words = [branch, RET] + [NOP] * 14 + [0x39404000, RET]
        fake = SimpleNamespace(text=(0, len(words) * 4), image=struct.pack(f'<{len(words)}I', *words),
                               build_id='00' * 16, syms=[], relocs=[])
        with patch.object(gen, 'Nso', return_value=fake), patch.object(gen, 'eh_frame_extents', return_value=[]):
            m = gen.Module('main', 'synthetic.nso', BASE, simd='c', locals_=locals_)
        m.patches = patches.table({'native_vr_hud_query': BASE})
        body = '\n'.join(m._emit_body(BASE, BASE + 8, set(), {}, set(), 'plain'))
        # Refuse to suppress a query whose destination is no longer the verified getter.
        original = m.nso.image
        m.nso.image = original[:64] + struct.pack('<II', NOP, RET)
        try:
            patches._native_vr_hud_query(m, BASE)
        except AssertionError:
            pass
        else:
            raise AssertionError('accepted a different native getter')
        source = f'''
#include <assert.h>
#include <string.h>
#include "cpu.h"
volatile int g_fnprof;
void fnprof_hit(uint64_t a, uint64_t b, uint64_t d) {{ (void)a; (void)b; (void)d; }}
static int sbs, calls;
int native_vr_restore_hud(void) {{ return sbs; }}
void rt_dispatch(Cpu *c) {{ assert(c->pc == {GETTER:#x}ull); calls++; c->x[0] = 1; }}
{body}
int main(void) {{
    for (sbs = 0; sbs <= 1; sbs++) {{
        Cpu c = {{0}}, expected;
        for (int i = 0; i < 31; i++) c.x[i] = 0x100 + i;
        c.sp = 0x1234; c.nzcv = 0xa0000000;
        expected = c;
        expected.x[0] = !sbs;
        expected.x[30] = {BASE + 4 if linked else 0x11e:#x}ull;
        if (!sbs) expected.pc = {GETTER:#x}ull;
        calls = 0;
        {gen.fname(BASE)}(&c);
        assert(calls == !sbs);
        assert(!memcmp(&c, &expected, sizeof c));
    }}
}}
'''
        with tempfile.TemporaryDirectory(prefix='native-vr-hud-') as directory:
            exe = Path(directory) / 'check.exe'
            subprocess.run([os.environ.get('CC', 'clang'), '-std=gnu11', '-O1', '-fno-strict-aliasing',
                            '-I', str(ROOT / 'runtime'), '-x', 'c', '-', '-o', str(exe), '-lm'],
                           input=source, text=True, check=True)
            subprocess.run([str(exe)], check=True)

print('ok: HUD-only B/BL queries, SBS/off behavior, preserved CPU state and getter guards, locals on/off')
