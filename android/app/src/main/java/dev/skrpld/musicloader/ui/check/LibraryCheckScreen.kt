package dev.skrpld.musicloader.ui.check

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularWavyProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearWavyProgressIndicator
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.WavyProgressIndicatorDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.CheckGroup
import dev.skrpld.musicloader.data.CheckItem
import dev.skrpld.musicloader.data.CheckReport
import dev.skrpld.musicloader.data.CheckRule
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.ui.components.SectionHeader
import dev.skrpld.musicloader.ui.rememberClipboardAccess
import kotlinx.coroutines.launch

/**
 * Settings → Check the library: the rules to check, the newest check with its progress or
 * report (problems grouped by kind, each track opening on its platform), and the Fix button.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun LibraryCheckScreen(
    viewModel: LibraryCheckViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val job by viewModel.job.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val failedFormat = stringResource(R.string.action_failed)
    var confirmFix by rememberSaveable { mutableStateOf(false) }
    // Open groups of the shown report, by kind.
    val expanded = remember(job?.id) { mutableStateMapOf<String, Boolean>() }

    LaunchedEffect(viewModel, snackbarHostState) {
        viewModel.failures.collect { message -> snackbarHostState.showSnackbar(failedFormat.format(message.orEmpty())) }
    }

    val scrollBehavior = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.check_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            painter = painterResource(R.drawable.ic_arrow_back),
                            contentDescription = stringResource(R.string.action_back),
                        )
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        val current = job
        val running = current != null && !current.status.isFinished
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(
                start = 16.dp,
                end = 16.dp,
                top = padding.calculateTopPadding() + 8.dp,
                bottom = padding.calculateBottomPadding() + 24.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap),
        ) {
            item(key = "description") {
                Text(
                    text = stringResource(R.string.check_description),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
                )
            }
            item(key = "rules-header") { SectionHeader(stringResource(R.string.check_rules)) }
            item(key = "rules") {
                RuleChips(selected = state.rules, enabled = !running, onToggle = viewModel::toggleRule)
            }
            item(key = "check") {
                Button(
                    onClick = viewModel::check,
                    enabled = !running && !state.submitting && state.rules.isNotEmpty(),
                    shapes = ButtonDefaults.shapes(),
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ic_search),
                        contentDescription = null,
                        modifier = Modifier.size(ButtonDefaults.IconSize),
                    )
                    Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                    Text(stringResource(R.string.action_check))
                }
            }
            when {
                current == null -> item(key = "never") { NeverChecked() }
                !current.status.isFinished ->
                    item(key = "progress") { CheckProgressCard(current, onCancel = viewModel::cancel) }
                else -> report(
                    job = current,
                    expanded = expanded,
                    fixEnabled = !state.submitting,
                    onFix = { confirmFix = true },
                )
            }
        }
    }

    val fixable = job?.check?.fixable ?: 0
    if (confirmFix && fixable > 0) {
        AlertDialog(
            onDismissRequest = { confirmFix = false },
            icon = { Icon(painterResource(R.drawable.ic_sync), contentDescription = null) },
            title = { Text(stringResource(R.string.check_fix_confirm_title, fixable)) },
            text = { Text(stringResource(R.string.check_fix_confirm_body)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmFix = false
                    viewModel.fix()
                }) { Text(stringResource(R.string.action_fix)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmFix = false }) { Text(stringResource(R.string.action_close)) }
            },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RuleChips(selected: Set<CheckRule>, enabled: Boolean, onToggle: (CheckRule) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            CheckRule.entries.forEach { rule ->
                FilterChip(
                    selected = rule in selected,
                    onClick = { onToggle(rule) },
                    enabled = enabled,
                    label = { Text(checkRuleName(rule)) },
                    leadingIcon = if (rule in selected) {
                        { Icon(painterResource(R.drawable.ic_check), contentDescription = null, modifier = Modifier.size(18.dp)) }
                    } else {
                        null
                    },
                )
            }
        }
        Text(
            text = stringResource(R.string.check_rule_available_hint),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 4.dp),
        )
    }
}

@Composable
private fun NeverChecked() {
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(stringResource(R.string.check_never), style = MaterialTheme.typography.titleMedium)
        Text(
            text = stringResource(R.string.check_never_body),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** The check (or its fix) while it runs: what it does now and how far it got. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun CheckProgress(job: Job, modifier: Modifier = Modifier) {
    val file = job.files.minByOrNull { it.slot }
    val fraction = (file?.percent ?: 0f) / 100f
    val animated by animateFloatAsState(
        targetValue = fraction.coerceIn(0f, 1f),
        animationSpec = WavyProgressIndicatorDefaults.ProgressAnimationSpec,
        label = "check",
    )
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            CircularWavyProgressIndicator(modifier = Modifier.size(48.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = stringResource(if (job.options.fix == true) R.string.check_fixing else R.string.check_running),
                    style = MaterialTheme.typography.titleMediumEmphasized,
                )
                if (file != null && file.label.isNotBlank()) {
                    Text(
                        text = file.label,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        LinearWavyProgressIndicator(progress = { animated }, modifier = Modifier.fillMaxWidth())
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun CheckProgressCard(job: Job, onCancel: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            CheckProgress(job)
            if (!job.cancelRequested && job.status == JobStatus.Running) {
                OutlinedButton(onClick = onCancel, shapes = ButtonDefaults.shapes(), modifier = Modifier.align(Alignment.End)) {
                    Text(stringResource(R.string.action_cancel_job))
                }
            }
        }
    }
}

private fun LazyListScope.report(
    job: Job,
    expanded: SnapshotStateMap<String, Boolean>,
    fixEnabled: Boolean,
    onFix: () -> Unit,
) {
    val report = job.check
    if (job.status != JobStatus.Completed || report == null) {
        item(key = "unfinished") { UnfinishedCard(job) }
        return
    }
    item(key = "report-header") { SectionHeader(stringResource(R.string.check_report)) }
    item(key = "summary") { SummaryCard(report, fixEnabled = fixEnabled, onFix = onFix) }
    report.groups.forEachIndexed { index, group ->
        val open = expanded[group.kind] == true
        item(key = "group-${group.kind}") {
            GroupItem(
                group = group,
                index = index,
                count = report.groups.size,
                open = open,
                onClick = { expanded[group.kind] = !open },
            )
        }
        if (open) {
            itemsIndexed(group.items, key = { position, _ -> "item-${group.kind}-$position" }) { position, item ->
                TrackItem(group, position, item)
            }
            val more = group.count - group.items.size
            if (more > 0) {
                item(key = "more-${group.kind}") {
                    Text(
                        text = stringResource(R.string.check_more, more),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 24.dp, top = 4.dp, bottom = 8.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun UnfinishedCard(job: Job) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                text = stringResource(
                    if (job.status == JobStatus.Cancelled) R.string.check_cancelled else R.string.check_failed,
                ),
                style = MaterialTheme.typography.titleMedium,
            )
            job.message?.let {
                Text(text = it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun SummaryCard(report: CheckReport, fixEnabled: Boolean, onFix: () -> Unit) {
    val clipboard = rememberClipboardAccess()
    val scope = rememberCoroutineScope()
    val logLabel = stringResource(R.string.check_log)
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = pluralStringResource(R.plurals.check_problems, report.problems, report.problems),
                style = MaterialTheme.typography.titleLargeEmphasized,
            )
            Text(
                text = pluralStringResource(R.plurals.check_files, report.tracks, report.tracks),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (report.unchecked > 0) {
                Text(
                    text = stringResource(R.string.check_unchecked, report.unchecked),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(text = checkFixNote(report), style = MaterialTheme.typography.bodyLarge)
            val log = report.log
            if (log != null) {
                TextButton(
                    onClick = { scope.launch { clipboard.write(logLabel, log) } },
                    modifier = Modifier.align(Alignment.Start),
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ic_content_copy),
                        contentDescription = stringResource(R.string.action_copy),
                        modifier = Modifier.size(ButtonDefaults.IconSize),
                    )
                    Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                    Text(text = "$logLabel: $log", maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            if (report.fixable > 0) {
                Button(
                    onClick = onFix,
                    enabled = fixEnabled,
                    shapes = ButtonDefaults.shapes(),
                    modifier = Modifier.align(Alignment.End),
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ic_sync),
                        contentDescription = null,
                        modifier = Modifier.size(ButtonDefaults.IconSize),
                    )
                    Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                    Text(stringResource(R.string.action_fix))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun GroupItem(group: CheckGroup, index: Int, count: Int, open: Boolean, onClick: () -> Unit) {
    val rotation by animateFloatAsState(if (open) 90f else 0f, label = "chevron")
    SegmentedListItem(
        onClick = onClick,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = count),
        leadingContent = { Icon(painterResource(checkGroupIcon(group.kind)), contentDescription = null) },
        supportingContent = {
            Text(
                if (group.fixable > 0) {
                    stringResource(R.string.check_fixable_count, group.fixable)
                } else {
                    stringResource(R.string.check_fix_none)
                },
            )
        },
        trailingContent = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = group.count.toString(), style = MaterialTheme.typography.titleMedium)
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    modifier = Modifier.rotate(rotation),
                )
            }
        },
    ) {
        Text(text = checkGroupName(group), maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun TrackItem(group: CheckGroup, position: Int, item: CheckItem) {
    val uriHandler = LocalUriHandler.current
    SegmentedListItem(
        onClick = { if (item.link.isNotBlank()) runCatching { uriHandler.openUri(item.link) } },
        shapes = ListItemDefaults.segmentedShapes(index = position, count = group.items.size),
        modifier = Modifier.padding(start = 16.dp),
        overlineContent = if (item.fixable) {
            { Text(stringResource(R.string.check_item_fixable)) }
        } else {
            null
        },
        supportingContent = {
            Text(
                text = listOf(item.path, item.detail).filter { it.isNotBlank() }.joinToString("\n"),
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        },
        trailingContent = if (item.link.isNotBlank()) {
            { Icon(painterResource(R.drawable.ic_link), contentDescription = null) }
        } else {
            null
        },
    ) {
        Text(text = item.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}
