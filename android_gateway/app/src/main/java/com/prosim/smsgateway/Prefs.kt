package com.prosim.smsgateway

import android.content.Context

/** Thin SharedPreferences wrapper for the gateway configuration. */
class Prefs(context: Context) {

    private val sp = context.applicationContext
        .getSharedPreferences("prosim_sms", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = sp.getString("base_url", "") ?: ""
        set(v) { sp.edit().putString("base_url", v.trim()).apply() }

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) { sp.edit().putString("token", v.trim()).apply() }

    var pollSeconds: Int
        get() = sp.getInt("poll_seconds", 10)
        set(v) { sp.edit().putInt("poll_seconds", v.coerceIn(3, 600)).apply() }

    var enabled: Boolean
        get() = sp.getBoolean("enabled", false)
        set(v) { sp.edit().putBoolean("enabled", v).apply() }

    val isConfigured: Boolean
        get() = baseUrl.isNotBlank() && token.isNotBlank()
}
