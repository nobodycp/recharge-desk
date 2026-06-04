package com.prosim.smsgateway

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.telephony.SmsManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Foreground service that drives the outbound side of the protocol: it polls
 * the server outbox, sends each reply as SMS, and reports delivery. Inbound is
 * handled directly by [SmsDeliverReceiver].
 */
class GatewayService : Service() {

    private lateinit var prefs: Prefs
    private var scheduler: ScheduledExecutorService? = null
    private val io = Executors.newSingleThreadExecutor()
    @Volatile private var polling = false

    private val sentReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val id = intent.getLongExtra(EXTRA_ID, -1L)
            if (id < 0) return
            val ok = resultCode == Activity.RESULT_OK
            val code = resultCode
            io.submit {
                try {
                    val api = ApiClient(prefs.baseUrl, prefs.token)
                    if (ok) api.delivery(sent = listOf(id))
                    else api.delivery(failed = listOf(id to "send_failed_$code"))
                } catch (e: Exception) {
                    Log.w(TAG, "delivery report failed", e)
                }
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        val filter = IntentFilter(ACTION_SMS_SENT)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(sentReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(sentReceiver, filter)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        startPolling()
        return START_STICKY
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.channel_name),
                NotificationManager.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(channel)
        }
        val tapIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notif_running))
            .setSmallIcon(R.drawable.ic_launcher)
            .setOngoing(true)
            .setContentIntent(tapIntent)
            .build()

        val type = if (Build.VERSION.SDK_INT >= 29) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        } else 0
        ServiceCompat.startForeground(this, NOTIF_ID, notification, type)
    }

    private fun startPolling() {
        if (scheduler != null) return
        val period = prefs.pollSeconds.toLong().coerceAtLeast(3)
        scheduler = Executors.newSingleThreadScheduledExecutor().also {
            it.scheduleWithFixedDelay({ pollOnce() }, 0, period, TimeUnit.SECONDS)
        }
    }

    private fun pollOnce() {
        if (polling) return
        polling = true
        try {
            if (!prefs.enabled || !prefs.isConfigured) return
            val api = ApiClient(prefs.baseUrl, prefs.token)
            val result = api.outbox(10)
            for (m in result.messages) sendSms(m)
            if (result.deleteIds.isNotEmpty()) {
                try {
                    api.delivery(deleted = result.deleteIds)
                } catch (_: Exception) {
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "poll failed", e)
        } finally {
            polling = false
        }
    }

    @Suppress("DEPRECATION")
    private fun smsManager(): SmsManager {
        return if (Build.VERSION.SDK_INT >= 31) {
            getSystemService(SmsManager::class.java)
        } else {
            SmsManager.getDefault()
        }
    }

    private fun sendSms(m: OutMsg) {
        val sm = smsManager()
        val parts = sm.divideMessage(m.body)
        val sentIntent = PendingIntent.getBroadcast(
            this, m.id.toInt(),
            Intent(ACTION_SMS_SENT).setPackage(packageName).putExtra(EXTRA_ID, m.id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        if (parts.size <= 1) {
            sm.sendTextMessage(m.to, null, m.body, sentIntent, null)
        } else {
            val sentIntents = ArrayList<PendingIntent?>()
            for (i in parts.indices) sentIntents.add(if (i == parts.size - 1) sentIntent else null)
            sm.sendMultipartTextMessage(m.to, null, parts, sentIntents, null)
        }
    }

    override fun onDestroy() {
        scheduler?.shutdownNow()
        scheduler = null
        try {
            unregisterReceiver(sentReceiver)
        } catch (_: Exception) {
        }
        io.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "GatewayService"
        private const val CHANNEL_ID = "prosim_sms_gateway"
        private const val NOTIF_ID = 1001
        const val ACTION_SMS_SENT = "com.prosim.smsgateway.SMS_SENT"
        const val EXTRA_ID = "outbound_id"

        fun ensureRunning(context: Context) {
            val intent = Intent(context, GatewayService::class.java)
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, GatewayService::class.java))
        }
    }
}
