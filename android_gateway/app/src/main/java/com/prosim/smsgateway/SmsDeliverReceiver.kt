package com.prosim.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.util.Log

/**
 * Fires only when this app is the default SMS app (SMS_DELIVER). We forward the
 * message to the server and deliberately never write it to the SMS provider, so
 * the gateway phone's inbox stays empty.
 */
class SmsDeliverReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_DELIVER_ACTION) return
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (messages.isEmpty()) return

        val sender = messages[0].displayOriginatingAddress
            ?: messages[0].originatingAddress
            ?: return
        val body = StringBuilder()
        for (m in messages) body.append(m.messageBody ?: "")
        val text = body.toString()

        val prefs = Prefs(context)
        if (!prefs.enabled || !prefs.isConfigured) return

        val deviceMsgId = sender + "-" + System.currentTimeMillis()
        val pending = goAsync()
        Thread {
            try {
                ApiClient(prefs.baseUrl, prefs.token).inbound(sender, text, deviceMsgId)
            } catch (e: Exception) {
                Log.w(TAG, "inbound forward failed", e)
            } finally {
                pending.finish()
            }
        }.start()
    }

    companion object {
        private const val TAG = "SmsDeliverReceiver"
    }
}
