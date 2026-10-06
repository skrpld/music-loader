package dev.skrpld.musicloader.ui

import android.content.ClipData
import android.os.Build
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.platform.LocalContext

/** Android-specific pieces used by the otherwise platform-neutral UI. */
val supportsDynamicColor: Boolean
    get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

@Composable
fun platformDynamicColorScheme(darkTheme: Boolean): ColorScheme? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return null
    val context = LocalContext.current
    return if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
}

class ClipboardAccess(
    val read: suspend () -> String?,
    val write: suspend (label: String, text: String) -> Unit,
)

@Composable
fun rememberClipboardAccess(): ClipboardAccess {
    val clipboard = LocalClipboard.current
    val context = LocalContext.current
    return remember(clipboard, context) {
        ClipboardAccess(
            read = {
                val clip = clipboard.getClipEntry()?.clipData
                if (clip == null || clip.itemCount == 0) {
                    null
                } else {
                    clip.getItemAt(0).coerceToText(context)?.toString()
                }
            },
            write = { label, text ->
                clipboard.setClipEntry(ClipEntry(ClipData.newPlainText(label, text)))
            },
        )
    }
}
