package dev.skrpld.musicloader.engine

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.DocumentsContract
import android.provider.Settings
import androidx.core.content.ContextCompat
import androidx.core.net.toUri

/**
 * Write access to the music folder. The downloader writes plain files - tracks, .lrc
 * lyrics, playlists and its index files - so Android 11+ needs "All files access";
 * older versions the storage permission.
 */
object StorageAccess {
    /** Needs [Manifest.permission.WRITE_EXTERNAL_STORAGE] instead of the settings screen. */
    val usesPermission: Boolean get() = Build.VERSION.SDK_INT < Build.VERSION_CODES.R

    const val PERMISSION = Manifest.permission.WRITE_EXTERNAL_STORAGE

    fun granted(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            ContextCompat.checkSelfPermission(context, PERMISSION) == PackageManager.PERMISSION_GRANTED
        }

    /** The "All files access" screen for this app (Android 11+), then the general one as a fallback. */
    fun settingsIntents(context: Context): List<Intent> = listOf(
        Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, "package:${context.packageName}".toUri()),
        Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
    )

    /**
     * The file path of a folder picked with the system picker, e.g.
     * "primary:Music/Rap" -> /storage/emulated/0/Music/Rap; null for folders that
     * are not on a storage volume (cloud providers).
     */
    fun pathOf(tree: Uri): String? {
        if (tree.authority != "com.android.externalstorage.documents") return null
        val id = runCatching { DocumentsContract.getTreeDocumentId(tree) }.getOrNull() ?: return null
        val volume = id.substringBefore(':')
        val relative = id.substringAfter(':', "").trim('/')
        @Suppress("DEPRECATION")
        val root = if (volume.equals("primary", ignoreCase = true)) {
            Environment.getExternalStorageDirectory().absolutePath
        } else {
            "/storage/$volume"
        }
        return if (relative.isEmpty()) root else "$root/$relative"
    }
}
