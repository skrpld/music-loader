package dev.skrpld.musicloader.ui.jobs

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.ButtonGroupDefaults
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.ToggleButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.LogEntry
import dev.skrpld.musicloader.ui.formatClock

/** "[Lyrics] No verified lyrics: ..."; error entries carry their tag as the source. */
private val tagPattern = Regex("""^\[([^\[\]]+)]""")

/** The line as it is shown, tag included. */
fun LogEntry.logLine(): String = if (source != null) "[$source] $text" else text

/** The tag at the start of the line ("Lyrics", "SoundCloud"), or null for an untagged line. */
fun LogEntry.logTag(): String? = source ?: tagPattern.find(text)?.groupValues?.get(1)

/**
 * Entries of [entries] that pass the filters, newest first: [tag] (null for any) and a
 * case-insensitive substring [query] (blank for any), matched against the whole line.
 */
fun filterLog(entries: List<LogEntry>, tag: String?, query: String): List<LogEntry> {
    val needle = query.trim()
    return entries.asReversed().filter { entry ->
        (tag == null || entry.logTag() == tag) && (needle.isEmpty() || entry.logLine().contains(needle, ignoreCase = true))
    }
}

/**
 * The job's log in a bordered block of its own height, scrolled inside the block. Level
 * (everything or errors only), tag and search filters combine.
 */
@Composable
fun ActivityLog(job: Job, modifier: Modifier = Modifier) {
    var errorsOnly by rememberSaveable(job.id) { mutableStateOf(false) }
    var selectedTag by rememberSaveable(job.id) { mutableStateOf<String?>(null) }
    var query by rememberSaveable(job.id) { mutableStateOf("") }
    // The server keeps more errors than log lines, so the error view reads its own list.
    val source = if (errorsOnly) job.errors else job.log
    val tags = remember(source) { source.mapNotNull { it.logTag() }.distinct().sortedBy { it.lowercase() } }
    // A tag missing from the current list does not hide everything.
    val tag = selectedTag?.takeIf { it in tags }
    val entries = remember(source, tag, query) { filterLog(source, tag, query) }

    OutlinedCard(modifier = modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            LevelFilter(errorsOnly = errorsOnly, errorCount = job.errorCount, onChange = { errorsOnly = it })
            if (tags.isNotEmpty()) {
                Row(
                    modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    FilterChip(
                        selected = tag == null,
                        onClick = { selectedTag = null },
                        label = { Text(stringResource(R.string.log_tag_all)) },
                    )
                    tags.forEach { name ->
                        FilterChip(
                            selected = name == tag,
                            onClick = { selectedTag = if (name == tag) null else name },
                            label = { Text(name) },
                        )
                    }
                }
            }
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                placeholder = { Text(stringResource(R.string.log_search)) },
                leadingIcon = { Icon(painterResource(R.drawable.ic_search), contentDescription = null) },
                trailingIcon = {
                    if (query.isNotEmpty()) {
                        IconButton(onClick = { query = "" }) {
                            Icon(
                                painter = painterResource(R.drawable.ic_close),
                                contentDescription = stringResource(R.string.action_clear_search),
                            )
                        }
                    }
                },
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Box(modifier = Modifier.fillMaxWidth().height(360.dp)) {
                if (entries.isEmpty()) {
                    Text(
                        text = stringResource(if (errorsOnly && source.isEmpty()) R.string.log_no_errors else R.string.log_no_matches),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.align(Alignment.Center),
                    )
                } else {
                    LazyColumn(modifier = Modifier.fillMaxSize()) {
                        items(entries, key = { it.seq }) { entry -> LogRow(entry) }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun LevelFilter(errorsOnly: Boolean, errorCount: Int, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        ToggleButton(
            checked = !errorsOnly,
            onCheckedChange = { onChange(false) },
            modifier = Modifier.weight(1f).semantics { role = Role.RadioButton },
            shapes = ButtonGroupDefaults.connectedLeadingButtonShapes(),
        ) {
            Text(text = stringResource(R.string.log_level_all), maxLines = 1)
        }
        ToggleButton(
            checked = errorsOnly,
            onCheckedChange = { onChange(true) },
            modifier = Modifier.weight(1f).semantics { role = Role.RadioButton },
            shapes = ButtonGroupDefaults.connectedTrailingButtonShapes(),
        ) {
            Text(text = stringResource(R.string.job_errors_count, errorCount), maxLines = 1)
        }
    }
}

@Composable
private fun LogRow(entry: LogEntry) {
    val color = if (entry.isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = formatClock(entry.time),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = entry.logLine(),
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
            color = color,
            modifier = Modifier.weight(1f),
        )
    }
}
