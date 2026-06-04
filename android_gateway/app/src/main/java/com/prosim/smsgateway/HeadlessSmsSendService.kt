package com.prosim.smsgateway

import android.app.Service
import android.content.Intent
import android.os.IBinder

/** Required to hold the default-SMS-app role. Quick replies are not supported. */
class HeadlessSmsSendService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        stopSelf()
        return START_NOT_STICKY
    }
}
