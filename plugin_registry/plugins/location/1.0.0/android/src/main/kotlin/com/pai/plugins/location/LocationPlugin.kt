package com.pai.plugins.location

import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.os.Bundle
import android.os.Looper
import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executor
import kotlin.coroutines.resume

/**
 * Location (`pai.location`): where the phone is. `location.get` returns a fix (latitude, longitude,
 * accuracy, age). It never starts continuous tracking: one fix per call, then the listener is removed.
 */
class LocationPlugin : PluginModule {

    override val name = "pai.location"
    override val capabilities = listOf("location.get")
    override val permissions = listOf("location")

    private var ctx: PluginContext? = null

    override fun attach(context: PluginContext) { ctx = context }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val c = ctx?.appContext ?: return failure("no_context_attached", "The location plugin is not ready")
        if (capability != "location.get") return failure("unknown_capability", "Unknown capability: $capability")
        return try {
            val precision = LocationRules.precision(params["precision"])
            val timeout = LocationRules.timeout(params["timeoutMs"])
            withContext(Dispatchers.Main) { get(c, precision, timeout) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: LocationError) {
            failure(e.code, e.message ?: e.code)
        } catch (e: SecurityException) {
            failure("permission_denied", "Location access has not been granted to PAI")
        } catch (t: Throwable) {
            Log.e(TAG, "location.get failed", t)
            failure("location_error", t.message ?: "Location failed")
        }
    }

    private suspend fun get(c: Context, precision: String, timeoutMs: Long): Map<String, Any?> {
        val lm = c.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val now = System.currentTimeMillis()
        val providers = lm.getProviders(true)
        if (providers.isEmpty()) throw LocationError("location_off", "Location is turned off on this phone. Turn it on in Android settings")

        val cached = providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.map(::fixOf)
        if (precision == "last_known") {
            val best = LocationRules.pickBest(cached, now) ?: throw LocationError("no_location", "The phone has no recent location")
            return LocationRules.toResult(best, now)
        }
        val wanted = if (precision == "high") listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                     else listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
        val provider = wanted.firstOrNull { it in providers } ?: providers.first()
        val fresh = withTimeoutOrNull(timeoutMs) { currentFix(lm, provider) }?.let(::fixOf)
        val candidates = listOfNotNull(fresh) + cached.filter { now - it.timeMs <= LocationRules.MAX_CACHED_AGE_MS }
        val best = LocationRules.pickBest(candidates, System.currentTimeMillis())
            ?: throw LocationError("no_location", "The phone could not find its location in time. Try near a window or outdoors")
        return LocationRules.toResult(best, System.currentTimeMillis())
    }

    @Suppress("MissingPermission")
    private suspend fun currentFix(lm: LocationManager, provider: String): Location? = suspendCancellableCoroutine { cont ->
        if (Build.VERSION.SDK_INT >= 30) {
            val signal = CancellationSignal()
            cont.invokeOnCancellation { signal.cancel() }
            lm.getCurrentLocation(provider, signal, Executor { it.run() }) { loc -> if (cont.isActive) cont.resume(loc) }
        } else {
            val listener = object : LocationListener {
                override fun onLocationChanged(l: Location) { lm.removeUpdates(this); if (cont.isActive) cont.resume(l) }
                @Deprecated("required below API 29") override fun onStatusChanged(p: String?, s: Int, e: Bundle?) {}
                override fun onProviderEnabled(p: String) {}
                override fun onProviderDisabled(p: String) {}
            }
            cont.invokeOnCancellation { lm.removeUpdates(listener) }
            @Suppress("DEPRECATION") lm.requestSingleUpdate(provider, listener, Looper.getMainLooper())
        }
    }

    private fun fixOf(l: Location) = Fix(l.latitude, l.longitude, if (l.hasAccuracy()) l.accuracy else null, l.provider ?: "unknown", l.time)

    private fun failure(code: String, message: String) = mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() { ctx = null }

    private companion object { const val TAG = "PaiLocationKt" }
}
