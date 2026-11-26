package com.pai.plugins.deviceinfo

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.os.SystemClock
import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.Locale
import java.util.TimeZone

/**
 * Device info (`pai.device`): `device.info` reports battery, network, storage and system facts of this
 * phone. Read-only and needs no Android permission (no Wi-Fi name, no identifiers, no location).
 */
class DeviceInfoPlugin : PluginModule {

    override val name = "pai.device"
    override val capabilities = listOf("device.info")
    override val permissions = emptyList<String>()

    private var ctx: PluginContext? = null

    override fun attach(context: PluginContext) { ctx = context }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val c = ctx?.appContext ?: return failure("no_context_attached", "The device plugin is not ready")
        if (capability != "device.info") return failure("unknown_capability", "Unknown capability: $capability")
        return try {
            val wanted = InfoRules.sections(params["sections"])
            withContext(Dispatchers.IO) {
                val out = linkedMapOf<String, Any?>("ok" to true)
                for (s in wanted) out[s] = when (s) {
                    "battery" -> battery(c); "network" -> network(c); "storage" -> storage(c); else -> system(c)
                }
                out
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: IllegalArgumentException) {
            failure("bad_parameter", e.message ?: "bad parameter")
        } catch (t: Throwable) {
            Log.e(TAG, "device.info failed", t)
            failure("device_error", t.message ?: "That did not work")
        }
    }

    private fun battery(c: Context): Map<String, Any?> {
        val i = c.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = i?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val plugged = i?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        return mapOf(
            "percent" to InfoRules.batteryPercent(i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1, i?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100),
            "charging" to (status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL),
            "plugged" to when { plugged and BatteryManager.BATTERY_PLUGGED_AC != 0 -> "ac"; plugged and BatteryManager.BATTERY_PLUGGED_USB != 0 -> "usb"
                plugged and BatteryManager.BATTERY_PLUGGED_WIRELESS != 0 -> "wireless"; else -> "no" },
            "temperature_c" to (i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)?.takeIf { it != Int.MIN_VALUE }?.let { it / 10.0 }))
    }

    private fun network(c: Context): Map<String, Any?> {
        val cm = c.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        val type = when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
            else -> "other"
        }
        return mapOf("connected" to (caps != null), "type" to type,
            "internet" to (caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true),
            "metered" to cm.isActiveNetworkMetered)
    }

    private fun storage(c: Context): Map<String, Any?> {
        fun stat(path: java.io.File): Map<String, Any?> {
            val s = StatFs(path.path); val total = s.totalBytes; val free = s.availableBytes
            return mapOf("total_bytes" to total, "free_bytes" to free, "used_percent" to InfoRules.usedPercent(total, free),
                "total" to InfoRules.human(total), "free" to InfoRules.human(free))
        }
        return mapOf("internal" to stat(Environment.getDataDirectory()))
    }

    private fun system(c: Context): Map<String, Any?> = mapOf(
        "manufacturer" to Build.MANUFACTURER, "model" to Build.MODEL, "android" to Build.VERSION.RELEASE, "sdk" to Build.VERSION.SDK_INT,
        "locale" to Locale.getDefault().toLanguageTag(), "time_zone" to TimeZone.getDefault().id,
        "uptime_hours" to Math.round(SystemClock.elapsedRealtime() / 360_000.0) / 10.0)

    private fun failure(code: String, message: String) = mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() { ctx = null }

    private companion object { const val TAG = "PaiDeviceInfoKt" }
}
