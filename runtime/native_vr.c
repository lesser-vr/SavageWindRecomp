// Apply the saved SBS choice on the game thread, once the field is playable.
#include "framepace.h"
#include "game_sites.h"
#include "hle.h"
#include "perfmon.h"
#include "settings.h"

static bool checked;  // Respect a manual goggles toggle until the next load or SBS selection.
static uint64_t enabled_manager;  // Only undo a request we made for this VR manager.

// These widgets keep their flat-screen positions in native VR. Bring them
// inside the rectangular eye crop, in layout units independent of output size.
typedef struct {
    unsigned screen;
    const char *name;
    float x, y;
    uint64_t root, pane;
    float saved[2];
} HudWidget;

static HudWidget hud_widgets[] = {
    {45, "N_MainAll_00", -389, 150},
    {37, "Pa_PlayerStatusUp_00", -360, 210},
};

static bool hud_ptr(uint64_t p) {
    return p >= 0x7000000000ull && p < 0x7320000000ull;
}

static uint64_t hud_pane(uint64_t root, const char *name, unsigned depth) {
    if (!hud_ptr(root) || depth > 16) return 0;
    if (!strncmp((const char *)(uintptr_t)(root + 0xb8), name, 40)) return root;
    uint64_t head = root + 0x20, link = LD64(head + 8);
    for (unsigned n = 0; n < 128 && hud_ptr(link) && link != head; n++, link = LD64(link + 8)) {
        uint64_t found = hud_pane(link - 8, name, depth + 1);
        if (found) return found;
    }
    return 0;
}

static void hud_layout(bool sbs) {
    uint64_t mgr = g_site.screen_mgr ? LD64(g_site.screen_mgr) : 0;
    uint32_t count = mgr ? LD32(mgr + 0x28) : 0;
    uint64_t screens = mgr ? LD64(mgr + 0x30) : 0;
    for (unsigned i = 0; i < sizeof hud_widgets / sizeof *hud_widgets; i++) {
        HudWidget *w = &hud_widgets[i];
        uint64_t screen = screens && w->screen < count ? LD64(screens + w->screen * 8) : 0;
        uint64_t layout = screen ? LD64(screen + 0x30) : 0;
        uint64_t root = layout ? LD64(layout + 0x18) : 0;
        if (root != w->root) {
            w->root = root;
            w->pane = 0;  // The old tree may already have been freed.
        }
        if (!sbs && !w->pane) continue;
        if (!w->pane) {
            w->pane = hud_pane(root, w->name, 0);
            if (!w->pane) continue;
            memcpy(w->saved, (void *)(uintptr_t)(w->pane + 0x30), sizeof w->saved);
        }
        float xy[2] = {w->x, w->y};
        const float *target = sbs ? xy : w->saved;
        if (memcmp((void *)(uintptr_t)(w->pane + 0x30), target, sizeof xy)) {
            memcpy((void *)(uintptr_t)(w->pane + 0x30), target, sizeof xy);
            // Pane::Calculate recomputes the matrix and propagates to children.
            ST8(w->pane + 0x58, LD8(w->pane + 0x58) | 0x10);
        }
        if (!sbs) w->pane = 0;
    }
}

// Only UI queries use this: the scene and camera must still see native VR.
int native_vr_restore_hud(void) {
    return g_settings.stereo_sbs != SBS_OFF;
}

RT_ON_RESET(native_vr_reset) {
    checked = false;
    enabled_manager = 0;
    for (unsigned i = 0; i < sizeof hud_widgets / sizeof *hud_widgets; i++)
        hud_widgets[i].root = hud_widgets[i].pane = 0;
}

void native_vr_frame(void) {
    bool sbs = g_settings.stereo_sbs != SBS_OFF;
    if (!strcmp(g_game_version, "1.9.0")) hud_layout(sbs);
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
