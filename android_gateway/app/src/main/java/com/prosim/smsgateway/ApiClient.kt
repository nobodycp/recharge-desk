package com.prosim.smsgateway

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

data class OutMsg(val id: Long, val to: String, val body: String)
data class OutboxResult(val messages: List<OutMsg>, val deleteIds: List<String>)

/**
 * Talks to the server's device API. Endpoints (see sms_gateway/api_urls.py):
 *   POST /sms-gateway/api/inbound/
 *   GET  /sms-gateway/api/outbox/?limit=N
 *   POST /sms-gateway/api/delivery/
 * Every request carries `Authorization: Bearer <token>`.
 */
class ApiClient(baseUrl: String, private val token: String) {

    private val base = baseUrl.trim().trimEnd('/')

    private fun open(path: String, method: String): HttpURLConnection {
        val c = URL(base + path).openConnection() as HttpURLConnection
        c.requestMethod = method
        c.connectTimeout = 15000
        c.readTimeout = 20000
        c.setRequestProperty("Authorization", "Bearer $token")
        c.setRequestProperty("Accept", "application/json")
        return c
    }

    private fun drain(c: HttpURLConnection): String {
        val stream = if (c.responseCode in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)
        return stream?.let {
            BufferedReader(InputStreamReader(it, Charsets.UTF_8)).use { r -> r.readText() }
        } ?: ""
    }

    private fun postJson(path: String, payload: JSONObject): Pair<Int, String> {
        val c = open(path, "POST")
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        c.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
        val code = c.responseCode
        val body = drain(c)
        c.disconnect()
        return code to body
    }

    fun inbound(from: String, text: String, deviceMsgId: String): Boolean {
        val payload = JSONObject()
            .put("from", from)
            .put("text", text)
            .put("device_msg_id", deviceMsgId)
        val (code, _) = postJson("/sms-gateway/api/inbound/", payload)
        return code in 200..299
    }

    fun outbox(limit: Int = 10): OutboxResult {
        val c = open("/sms-gateway/api/outbox/?limit=$limit", "GET")
        val code = c.responseCode
        val body = drain(c)
        c.disconnect()
        if (code !in 200..299) throw RuntimeException("outbox HTTP $code")
        val json = JSONObject(body)
        val messages = ArrayList<OutMsg>()
        val arr = json.optJSONArray("messages") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val m = arr.getJSONObject(i)
            messages.add(OutMsg(m.getLong("id"), m.getString("to"), m.getString("body")))
        }
        val deletes = ArrayList<String>()
        val darr = json.optJSONArray("delete_ids") ?: JSONArray()
        for (i in 0 until darr.length()) deletes.add(darr.getString(i))
        return OutboxResult(messages, deletes)
    }

    fun delivery(
        sent: List<Long> = emptyList(),
        failed: List<Pair<Long, String>> = emptyList(),
        deleted: List<String> = emptyList(),
    ): Boolean {
        val obj = JSONObject()
        obj.put("sent", JSONArray(sent))
        val farr = JSONArray()
        for ((id, err) in failed) farr.put(JSONObject().put("id", id).put("error", err))
        obj.put("failed", farr)
        obj.put("deleted", JSONArray(deleted))
        val (code, _) = postJson("/sms-gateway/api/delivery/", obj)
        return code in 200..299
    }

    /** Lightweight connectivity/auth check used by the "Test" button. */
    fun ping(): Pair<Boolean, String> {
        return try {
            val c = open("/sms-gateway/api/outbox/?limit=1", "GET")
            val code = c.responseCode
            c.disconnect()
            when (code) {
                in 200..299 -> true to "OK ($code)"
                401 -> false to "401 — توكن غير صحيح"
                else -> false to "HTTP $code"
            }
        } catch (e: Exception) {
            false to (e.message ?: "خطأ اتصال")
        }
    }
}
