package com.prosim.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Restart the gateway service after a reboot when enabled. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val prefs = Prefs(context)
        if (prefs.enabled && prefs.isConfigured) {
            GatewayService.ensureRunning(context)
        }
    }
}
