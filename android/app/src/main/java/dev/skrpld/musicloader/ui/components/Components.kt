package dev.skrpld.musicloader.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialShapes
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.toShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Connection

@Composable
fun SectionHeader(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleSmallEmphasized,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(start = 4.dp, top = 12.dp, bottom = 4.dp),
    )
}

/** An icon in an expressive "cookie" shape, used by empty states. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ShapedIcon(icon: Int, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .size(112.dp)
            .clip(MaterialShapes.Cookie9Sided.toShape())
            .background(MaterialTheme.colorScheme.primaryContainer),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            painter = painterResource(icon),
            contentDescription = null,
            modifier = Modifier.size(48.dp),
            tint = MaterialTheme.colorScheme.onPrimaryContainer,
        )
    }
}

@Composable
fun EmptyState(
    icon: Int,
    title: String,
    body: String?,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ShapedIcon(icon)
        Text(
            text = title,
            style = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center,
        )
        if (body != null) {
            Text(
                text = body,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
        action?.invoke()
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun CenteredLoading(modifier: Modifier = Modifier) {
    Box(modifier = modifier.fillMaxWidth().padding(48.dp), contentAlignment = Alignment.Center) {
        LoadingIndicator()
    }
}

/** A small rounded label: an icon and a short text on a tonal background. */
@Composable
fun Pill(
    text: String,
    modifier: Modifier = Modifier,
    icon: Int? = null,
    containerColor: Color = MaterialTheme.colorScheme.secondaryContainer,
    contentColor: Color = MaterialTheme.colorScheme.onSecondaryContainer,
) {
    Surface(modifier = modifier, shape = CircleShape, color = containerColor, contentColor = contentColor) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (icon != null) {
                Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(16.dp))
            }
            Text(text = text, style = MaterialTheme.typography.labelLarge, maxLines = 1)
        }
    }
}

/** Explains why there is no live connection and offers the way out. Nothing when online. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ConnectionBanner(
    connection: Connection,
    onRetry: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (connection) {
        is Connection.Online -> Unit
        Connection.Connecting -> BannerCard(
            modifier = modifier,
            title = stringResource(R.string.connection_connecting),
            body = null,
            leading = { LoadingIndicator(modifier = Modifier.size(40.dp)) },
        )
        Connection.NotConfigured -> BannerCard(
            modifier = modifier,
            title = stringResource(R.string.connection_not_configured),
            body = stringResource(R.string.connection_not_configured_body),
            leading = { BannerIcon(R.drawable.ic_dns) },
            actionLabel = stringResource(R.string.action_configure),
            onAction = onOpenSettings,
        )
        is Connection.Offline -> if (connection.unauthorized) {
            BannerCard(
                modifier = modifier,
                title = stringResource(R.string.connection_unauthorized),
                body = stringResource(R.string.connection_unauthorized_body),
                leading = { BannerIcon(R.drawable.ic_error) },
                actionLabel = stringResource(R.string.action_open_settings),
                onAction = onOpenSettings,
                isError = true,
            )
        } else {
            BannerCard(
                modifier = modifier,
                title = stringResource(R.string.connection_offline),
                body = connection.message ?: stringResource(R.string.connection_offline_body),
                leading = { BannerIcon(R.drawable.ic_cloud_off) },
                actionLabel = stringResource(R.string.action_retry),
                onAction = onRetry,
                isError = true,
            )
        }
    }
}

@Composable
private fun BannerIcon(icon: Int) {
    Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(24.dp))
}

@Composable
private fun BannerCard(
    title: String,
    body: String?,
    leading: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: () -> Unit = {},
    isError: Boolean = false,
) {
    val colors = if (isError) {
        CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
            contentColor = MaterialTheme.colorScheme.onErrorContainer,
        )
    } else {
        CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        )
    }
    Card(modifier = modifier.fillMaxWidth(), colors = colors, shape = MaterialTheme.shapes.large) {
        Row(
            modifier = Modifier.padding(start = 16.dp, end = 8.dp, top = 12.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            leading()
            Column(modifier = Modifier.weight(1f)) {
                Text(text = title, style = MaterialTheme.typography.titleSmall)
                if (body != null) {
                    Text(
                        text = body,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (actionLabel != null) {
                TextButton(onClick = onAction) { Text(actionLabel) }
            }
        }
    }
}
