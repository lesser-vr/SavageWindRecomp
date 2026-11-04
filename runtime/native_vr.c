// Apply the saved SBS choice on the game thread, once the field is playable.
#include "framepace.h"
#include "game_sites.h"
#include "hle.h"
#include "perfmon.h"
#include "settings.h"

static bool checked;  // Respect a manual goggles toggle until the next load or SBS selection.
static uint64_t enabled_manager;  // Only undo a request we made for this VR manager.

RT_ON_RESET(native_vr_reset) {
    checked = false;
    enabled_manager = 0;
}

void native_vr_frame(void) {
    bool sbs = g_settings.stereo_sbs != SBS_OFF;
    if (!sbs && !enabled_manager) {
        checked = false;
        return;
    }
    if (strcmp(g_game_version, "1.9.0")) return;
    if (framepace_title_ui() || framepace_loading_ui()) {
        checked = false;
        return;
    }
    if (sbs && checked) return;  // Half <-> Full needs no game change.

    float pos[3];
    if (framepace_paused() || !perfmon_position(pos) || (!pos[0] && !pos[1] && !pos[2])) return;
    // BOTW 1.9.0: the native Options confirmation (main+0xe2fd04) gets the
    // graphics singleton's VR manager, queues the mode and updates the UI.
    uint64_t graphics = LD64(MAIN_BASE + 0x1e43df0);
    uint64_t vr = graphics ? LD64(graphics + 0x380) : 0;
    uint64_t screens = LD64(g_site.screen_mgr);
    if (!vr || !screens || !LD32(screens + 0x28) || !LD64(screens + 0x30)) return;

    if (vr != enabled_manager) enabled_manager = 0;
    bool goggles_on = LD8(vr + 0x10) != 0;
    if (goggles_on != sbs && (sbs || enabled_manager)) {
        Cpu *cpu = kernel_current_cpu();
        Cpu saved = *cpu;
        cpu->x[0] = vr;
        cpu->x[1] = sbs;
        guest_call(cpu, MAIN_BASE + 0x1260e20);
        cpu->x[0] = sbs;
        guest_call(cpu, MAIN_BASE + 0xe8a360);
        cpu->x[0] = sbs;
        guest_call(cpu, MAIN_BASE + 0xe8a410);
        *cpu = saved;
        enabled_manager = vr;
        LOG("[stereo] SBS requested Toy-Con VR goggles %s\n", sbs ? "on" : "off");
    }
    if (!sbs) enabled_manager = 0;
    checked = sbs;
}
