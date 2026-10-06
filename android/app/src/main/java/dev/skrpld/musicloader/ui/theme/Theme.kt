package dev.skrpld.musicloader.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialExpressiveTheme
import androidx.compose.material3.MotionScheme
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import dev.skrpld.musicloader.data.ThemeMode
import dev.skrpld.musicloader.ui.platformDynamicColorScheme

@Composable
fun ThemeMode.isDark(): Boolean = when (this) {
    ThemeMode.System -> isSystemInDarkTheme()
    ThemeMode.Light -> false
    ThemeMode.Dark -> true
}

/**
 * Material 3 Expressive theme: wallpaper-based dynamic colors on Android 12+ (when
 * enabled), the app's own scheme otherwise, and the expressive motion scheme.
 */
@Composable
fun MusicLoaderTheme(
    darkTheme: Boolean,
    dynamicColor: Boolean,
    content: @Composable () -> Unit,
) {
    val colorScheme = (if (dynamicColor) platformDynamicColorScheme(darkTheme) else null)
        ?: if (darkTheme) DarkColors else LightColors
    MaterialExpressiveTheme(
        colorScheme = colorScheme,
        motionScheme = MotionScheme.expressive(),
        typography = Typography(),
        content = content,
    )
}
