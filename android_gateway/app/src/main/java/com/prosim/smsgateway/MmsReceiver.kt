package com.prosim.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Required to hold the default-SMS-app role. MMS is not supported. */
class MmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // No-op: the gateway only handles plain text SMS.
    }
}
