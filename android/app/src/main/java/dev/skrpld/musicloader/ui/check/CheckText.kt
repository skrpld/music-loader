package dev.skrpld.musicloader.ui.check

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.CheckGroup
import dev.skrpld.musicloader.data.CheckReport
import dev.skrpld.musicloader.data.CheckRule

@Composable
fun checkRuleName(rule: CheckRule): String = stringResource(
    when (rule) {
        CheckRule.Id -> R.string.check_rule_id
        CheckRule.Cover -> R.string.check_rule_cover
        CheckRule.Lyrics -> R.string.check_rule_lyrics
        CheckRule.Artist -> R.string.check_rule_artist
        CheckRule.Format -> R.string.check_rule_format
        CheckRule.Duplicates -> R.string.check_rule_duplicates
        CheckRule.Available -> R.string.check_rule_available
    },
)

/** The group's name in the app's language; a kind this app does not know keeps the server's label. */
@Composable
fun checkGroupName(group: CheckGroup): String {
    val id = when (group.kind) {
        "no_id" -> R.string.check_kind_no_id
        "no_cover" -> R.string.check_kind_no_cover
        "no_lyrics" -> R.string.check_kind_no_lyrics
        "artist_changed" -> R.string.check_kind_artist_changed
        "artist_unknown" -> R.string.check_kind_artist_unknown
        "old_format" -> R.string.check_kind_old_format
        "duplicate" -> R.string.check_kind_duplicate
        "removed" -> R.string.check_kind_removed
        else -> return group.label.ifBlank { group.kind }
    }
    return stringResource(id)
}

fun checkGroupIcon(kind: String): Int = when (kind) {
    "no_id" -> R.drawable.ic_link
    "no_cover" -> R.drawable.ic_library_music
    "no_lyrics" -> R.drawable.ic_lyrics
    "artist_changed", "artist_unknown" -> R.drawable.ic_person
    "old_format" -> R.drawable.ic_sync
    "duplicate" -> R.drawable.ic_content_copy
    "removed" -> R.drawable.ic_cloud_off
    else -> R.drawable.ic_warning
}

/** What the fix did, or what it can do: the last line of the report, as in the command line. */
@Composable
fun checkFixNote(report: CheckReport): String {
    val fixed = report.fixed
    val before = report.before
    return when {
        report.fix && fixed != null && before != null ->
            stringResource(R.string.check_fixed_note, fixed, before, report.problems)
        report.problems == 0 -> stringResource(R.string.check_no_problems)
        report.fixable == 0 -> stringResource(R.string.check_fix_none)
        else -> stringResource(R.string.check_fix_note, report.fixable, report.problems)
    }
}
