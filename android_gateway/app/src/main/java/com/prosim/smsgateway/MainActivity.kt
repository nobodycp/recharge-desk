package com.prosim.smsgateway

import android.Manifest
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Telephony
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var baseUrlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var pollInput: EditText
    private lateinit var enabledCheck: CheckBox
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)

        baseUrlInput = findViewById(R.id.input_base_url)
        tokenInput = findViewById(R.id.input_token)
        pollInput = findViewById(R.id.input_poll)
        enabledCheck = findViewById(R.id.check_enabled)
        statusText = findViewById(R.id.text_status)

        baseUrlInput.setText(prefs.baseUrl)
        tokenInput.setText(prefs.token)
        pollInput.setText(prefs.pollSeconds.toString())
        enabledCheck.isChecked = prefs.enabled

        findViewById<Button>(R.id.btn_save).setOnClickListener { save() }
        findViewById<Button>(R.id.btn_test).setOnClickListener { test() }
        findViewById<Button>(R.id.btn_permissions).setOnClickListener { requestRuntimePermissions() }
        findViewById<Button>(R.id.btn_default_sms).setOnClickListener { requestDefaultSmsApp() }

        requestRuntimePermissions()
        refreshStatus()
    }

    private fun save() {
        prefs.baseUrl = baseUrlInput.text.toString()
        prefs.token = tokenInput.text.toString()
        prefs.pollSeconds = pollInput.text.toString().toIntOrNull() ?: 10
        prefs.enabled = enabledCheck.isChecked
        pollInput.setText(prefs.pollSeconds.toString())

        if (prefs.enabled && prefs.isConfigured) {
            GatewayService.ensureRunning(this)
            Toast.makeText(this, R.string.saved_running, Toast.LENGTH_SHORT).show()
        } else {
            GatewayService.stop(this)
            Toast.makeText(this, R.string.saved_stopped, Toast.LENGTH_SHORT).show()
        }
        refreshStatus()
    }

    private fun test() {
        val baseUrl = baseUrlInput.text.toString()
        val token = tokenInput.text.toString()
        if (baseUrl.isBlank() || token.isBlank()) {
            statusText.text = getString(R.string.test_need_config)
            return
        }
        statusText.text = getString(R.string.testing)
        Thread {
            val (ok, msg) = ApiClient(baseUrl, token).ping()
            runOnUiThread {
                statusText.text = if (ok) getString(R.string.test_ok, msg)
                else getString(R.string.test_fail, msg)
            }
        }.start()
    }

    private fun requestRuntimePermissions() {
        val needed = mutableListOf(
            Manifest.permission.RECEIVE_SMS,
            Manifest.permission.READ_SMS,
            Manifest.permission.SEND_SMS,
        )
        if (Build.VERSION.SDK_INT >= 33) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        val missing = needed.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), REQ_PERMS)
        }
    }

    private fun requestDefaultSmsApp() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val rm = getSystemService(RoleManager::class.java)
            if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_SMS)) {
                if (rm.isRoleHeld(RoleManager.ROLE_SMS)) {
                    Toast.makeText(this, R.string.already_default, Toast.LENGTH_SHORT).show()
                } else {
                    startActivityForResult(
                        rm.createRequestRoleIntent(RoleManager.ROLE_SMS),
                        REQ_DEFAULT_SMS,
                    )
                }
            }
        } else {
            val intent = Intent(Telephony.Sms.Intents.ACTION_CHANGE_DEFAULT)
                .putExtra(Telephony.Sms.Intents.EXTRA_PACKAGE_NAME, packageName)
            startActivity(intent)
        }
    }

    private fun isDefaultSmsApp(): Boolean {
        return packageName == Telephony.Sms.getDefaultSmsPackage(this)
    }

    private fun refreshStatus() {
        val running = prefs.enabled && prefs.isConfigured
        val def = if (isDefaultSmsApp()) getString(R.string.yes) else getString(R.string.no)
        val state = if (running) getString(R.string.on) else getString(R.string.off)
        statusText.text = getString(R.string.status_line, state, def)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    companion object {
        private const val REQ_PERMS = 100
        private const val REQ_DEFAULT_SMS = 101
    }
}
