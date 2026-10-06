package dev.skrpld.musicloader.ui.jobs

import androidx.activity.compose.PredictiveBackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.ui.components.EmptyState
import kotlin.coroutines.cancellation.CancellationException

/** Width from which the job list and the job details are shown side by side. */
private val TwoPaneMinWidth = 840.dp

/**
 * The jobs destination: the list, and the details of the selected job either on top of it
 * (phones, with a predictive back animation) or next to it (tablets, foldables, desktop).
 */
@Composable
fun JobsPane(
    viewModel: JobsViewModel,
    onAddLinks: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val selectedId by viewModel.selectedJobId.collectAsStateWithLifecycle()
    val selected by viewModel.selectedJob.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val snackbarHostState = remember { SnackbarHostState() }
    var confirmCancel by remember { mutableStateOf<Job?>(null) }
    var confirmDelete by remember { mutableStateOf<Job?>(null) }

    val failedFormat by rememberUpdatedState(stringResource(R.string.action_failed))
    LaunchedEffect(viewModel, snackbarHostState) {
        viewModel.events.collect { event ->
            when (event) {
                is JobsEvent.ActionFailed -> snackbarHostState.showSnackbar(failedFormat.format(event.message.orEmpty()))
            }
        }
    }
    // A job deleted on the server (or from another device) closes its details.
    LaunchedEffect(selected?.gone) {
        if (selected?.gone == true) viewModel.select(null)
    }

    val list: @Composable (Modifier) -> Unit = { listModifier ->
        JobsScreen(
            connection = connection,
            listState = listState,
            selectedId = selectedId,
            onSelect = viewModel::select,
            onCancel = { confirmCancel = it },
            onAddLinks = onAddLinks,
            onOpenSettings = onOpenSettings,
            onRetry = viewModel::retry,
            modifier = listModifier,
        )
    }
    val detail: @Composable (Modifier, Boolean) -> Unit = { detailModifier, showBack ->
        JobDetailScreen(
            selected = selected,
            showBack = showBack,
            onBack = { viewModel.select(null) },
            onCancel = { confirmCancel = it },
            onDelete = { confirmDelete = it },
            modifier = detailModifier,
        )
    }

    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        if (maxWidth >= TwoPaneMinWidth) {
            Row(modifier = Modifier.fillMaxSize()) {
                list(Modifier.weight(0.45f).fillMaxHeight())
                VerticalDivider()
                if (selectedId != null) {
                    detail(Modifier.weight(0.55f).fillMaxHeight(), false)
                } else {
                    EmptyState(
                        icon = R.drawable.ic_queue_music,
                        title = stringResource(R.string.jobs_select_hint),
                        body = null,
                        modifier = Modifier.weight(0.55f),
                    )
                }
            }
        } else {
            var backProgress by remember { mutableFloatStateOf(0f) }
            PredictiveBackHandler(enabled = selectedId != null) { progress ->
                try {
                    progress.collect { event -> backProgress = event.progress }
                    viewModel.select(null)
                } catch (e: CancellationException) {
                    throw e
                } finally {
                    backProgress = 0f
                }
            }
            val motion = MaterialTheme.motionScheme
            AnimatedContent(
                targetState = selectedId != null,
                transitionSpec = {
                    (fadeIn(motion.defaultEffectsSpec()) + scaleIn(motion.defaultSpatialSpec(), initialScale = 0.96f))
                        .togetherWith(fadeOut(motion.fastEffectsSpec()))
                },
                label = "jobs",
            ) { showDetail ->
                if (showDetail) {
                    detail(
                        Modifier.fillMaxSize().graphicsLayer {
                            val scale = 1f - 0.1f * backProgress
                            scaleX = scale
                            scaleY = scale
                            translationX = backProgress * 32.dp.toPx()
                            shape = RoundedCornerShape(32.dp * backProgress)
                            clip = backProgress > 0f
                        },
                        true,
                    )
                } else {
                    list(Modifier.fillMaxSize())
                }
            }
        }
        SnackbarHost(snackbarHostState, modifier = Modifier.align(Alignment.BottomCenter))
    }

    confirmCancel?.let { job ->
        AlertDialog(
            onDismissRequest = { confirmCancel = null },
            icon = { Icon(painterResource(R.drawable.ic_cancel), contentDescription = null) },
            title = { Text(stringResource(R.string.cancel_dialog_title)) },
            text = {
                Text(
                    stringResource(
                        if (job.status == JobStatus.Running) R.string.cancel_dialog_running else R.string.cancel_dialog_queued,
                    ),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.cancel(job.id)
                    confirmCancel = null
                }) { Text(stringResource(R.string.action_cancel_job)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmCancel = null }) { Text(stringResource(R.string.action_keep)) }
            },
        )
    }
    confirmDelete?.let { job ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            icon = { Icon(painterResource(R.drawable.ic_delete), contentDescription = null) },
            title = { Text(stringResource(R.string.delete_dialog_title)) },
            text = { Text(stringResource(R.string.delete_dialog_body)) },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.delete(job.id)
                    confirmDelete = null
                }) { Text(stringResource(R.string.action_delete_job)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmDelete = null }) { Text(stringResource(R.string.action_keep)) }
            },
        )
    }
}
