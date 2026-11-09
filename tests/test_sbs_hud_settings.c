// Run with a temporary INI; the second process checks persistence from the first.
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "../runtime/settings.c"
#include "../runtime/hle/bindings.c"

const TextLanguage g_text_languages[] = {{"Auto", NULL, NULL}};
const VoiceLanguage g_voice_languages[] = {{"Auto", NULL}};
const int g_ntext_languages = 1, g_nvoice_languages = 1;
int input_pad_family(void) { return 0; }
bool fsr3_upscaler_enabled(void) { return false; }
float winsize_refresh_rate(void) { return 0; }

int main(int argc, char **argv) {
    assert(argc == 3);
    setenv("SAVAGEWIND_SETTINGS", argv[1], 1);
    assert(g_settings.sbs_hud == SBS_HUD_FULL);
    settings_load();
    assert(g_settings.sbs_hud == atoi(argv[2]));
    g_settings.sbs_hud = g_settings.sbs_hud == SBS_HUD_FULL ? SBS_HUD_ORIGINAL : SBS_HUD_FULL;
    settings_save();
    puts("PASS: SBS HUD default and persisted selection");
}
