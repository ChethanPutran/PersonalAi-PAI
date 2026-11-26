package com.pai.plugins.computercontrol

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.pai.plugin.api.AccessibilityBridge
import com.pai.plugin.api.PaiHost
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.util.UUID

/**
 * Computer control for Android (`pai.computer_control`): the phone side of docs/computer_control.md.
 *
 * The plugin is a set of small, stateless actions. The perception -> action -> verification loop lives in
 * the backend agent, which calls them as tools: observe, act, observe again. Everything works through the
 * host's Accessibility Service ([PaiHost.accessibility]), which the user switches on in Android Settings;
 * until then every action says so (with how to fix it) instead of pretending.
 *
 * Safe by construction: it never types into password fields, never reports their text, logs what it does
 * but not what it types, and serialises actions (one at a time).
 */
class ComputerControlPlugin : PluginModule {

    override val name = "pai.computer_control"
    override val capabilities = listOf(
        "computer.status", "computer.observe", "computer.click", "computer.long_press", "computer.type",
        "computer.key", "computer.scroll", "computer.swipe", "computer.apps.list", "computer.apps.launch", "computer.apps.foreground",
    )
    // Logical permissions for the policy layer; neither maps to an Android runtime permission (the
    // Accessibility Service is the user's own switch in system settings).
    override val permissions = listOf("screen", "input")

    private var ctx: PluginContext? = null
    private val lock = Mutex()
    @Volatile private var last: Observation? = null

    override fun attach(context: PluginContext) { ctx = context }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val c = ctx?.appContext ?: return failure(ControlError("no_context_attached", "The computer-control plugin is not ready"))
        return try {
            when (capability) {
                "computer.status" -> status()
                "computer.apps.list" -> withContext(Dispatchers.IO) { appsList(c, params) }
                "computer.apps.launch" -> withContext(Dispatchers.IO) { appsLaunch(c, params) }
                else -> {
                    val bridge = PaiHost.accessibility ?: throw notEnabled()
                    lock.withLock {
                        when (capability) {
                            "computer.apps.foreground" -> withContext(Dispatchers.IO) { foreground(c, bridge) }
                            "computer.observe" -> withContext(Dispatchers.IO) { observe(bridge, params) }
                            "computer.click" -> click(bridge, params)
                            "computer.long_press" -> longPress(bridge, params)
                            "computer.type" -> withContext(Dispatchers.IO) { type(bridge, params) }
                            "computer.key" -> key(bridge, params)
                            "computer.scroll" -> scroll(bridge, params)
                            "computer.swipe" -> swipe(bridge, params)
                            else -> throw ControlError("unknown_capability", "Unknown capability: $capability")
                        }
                    }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ControlError) {
            failure(e)
        } catch (e: SecurityException) {
            failure(ControlError("permission_denied", "Android refused that (${e.message ?: "security"})"))
        } catch (t: Throwable) {
            Log.e(TAG, "invoke($capability) failed", t)
            failure(ControlError("control_error", t.message ?: "That did not work"))
        }
    }

    // ------------------------------------------------------------------ status

    private fun status(): Map<String, Any?> {
        val bridge = PaiHost.accessibility
        val ready = bridge != null
        return mapOf(
            "ok" to true, "ready" to ready, "platform" to "android", "sdk" to Build.VERSION.SDK_INT,
            "missing" to if (ready) emptyList<String>() else listOf("accessibility_service"),
            "how_to_fix" to if (ready) null else HOW_TO_FIX,
            "features" to mapOf("tree" to ready, "screenshot" to (ready && Build.VERSION.SDK_INT >= 30), "ocr" to false, "input" to ready))
    }

    private fun notEnabled() = ControlError("accessibility_not_enabled",
        "PAI cannot see or control the screen yet: the PAI accessibility service is off", HOW_TO_FIX)

    // ------------------------------------------------------------------ observe

    private suspend fun observe(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val includeTree = bool(p["include_tree"], true)
        val includeShot = bool(p["include_screenshot"], false)
        val maxNodes = Gestures.intIn(p["max_nodes"], "max_nodes", TreeBuilder.DEFAULT_MAX_NODES, 1, 1000)
        val maxDepth = Gestures.intIn(p["max_depth"], "max_depth", TreeBuilder.DEFAULT_MAX_DEPTH, 1, 40)
        val (w, h) = bridge.screenSize()
        val root = bridge.activeRoot()
        val built = if (includeTree) TreeBuilder.build(root?.let(::AccessNode), maxNodes, maxDepth) else BuiltTree(emptyList(), emptyMap(), false)
        val id = UUID.randomUUID().toString().take(12)
        last = Observation(id, built.nodes)
        val pkg = root?.packageName?.toString() ?: bridge.activePackage()
        val shot = if (includeShot) screenshot(bridge) else null
        return linkedMapOf(
            "ok" to true,
            "observation_id" to id,
            "app" to mapOf("id" to pkg, "name" to pkg?.let { label(ctx!!.appContext, it) }, "title" to windowTitle(root)),
            "screen" to mapOf("width" to w, "height" to h),
            "tree" to if (includeTree) built.tree else null,
            "truncated" to built.truncated,
            "screenshot" to shot?.first,
            "screenshot_error" to shot?.second,
        ).also { if (root == null) it["note"] = "No window is readable right now (a secure screen, or the screen is off)" }
    }

    private fun windowTitle(root: AccessibilityNodeInfo?): String? = root?.window?.title?.toString()

    /** (screenshot map, error) - exactly one is non-null when a screenshot was asked for. */
    private suspend fun screenshot(bridge: AccessibilityBridge): Pair<Map<String, Any?>?, String?> {
        if (Build.VERSION.SDK_INT < 30) return null to "Screenshots need Android 11 or newer"
        val bmp = bridge.takeScreenshot() ?: return null to "Android did not allow a screenshot right now (a secure screen, or too soon after the last one)"
        val (tw, th) = ImageRules.targetSize(bmp.width, bmp.height, MAX_SHOT_SIDE)
        val scaled = if (tw != bmp.width) Bitmap.createScaledBitmap(bmp, tw, th, true) else bmp
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.PNG, 100, out)
        if (scaled !== bmp) scaled.recycle()
        bmp.recycle()
        return mapOf("mime" to "image/png", "base64" to Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP), "width" to tw, "height" to th) to null
    }

    // ------------------------------------------------------------------ targets

    private class Target(val node: AccessNode?, val id: String?, val x: Float, val y: Float)

    /** `element_id` of the latest observation, or `x`,`y` in its pixels. */
    private fun target(bridge: AccessibilityBridge, p: Map<String, Any?>): Target {
        val id = p["element_id"] as? String
        if (id != null) {
            val obs = last
            val wanted = p["observation_id"] as? String
            if (obs == null || (wanted != null && wanted != obs.id) || obs.nodes[id] == null) {
                throw ControlError("stale_element", "That element is from an earlier screen. Call computer.observe again and use the new ids")
            }
            val node = obs.nodes[id] as AccessNode
            if (!node.refresh()) throw ControlError("stale_element", "That element is no longer on screen. Call computer.observe again")
            val (x, y) = TreeBuilder.center(node)
            return Target(node, id, x, y)
        }
        val x = (p["x"] as? Number)?.toFloat(); val y = (p["y"] as? Number)?.toFloat()
        if (x == null || y == null) throw ControlError("bad_parameter", "Give element_id (from computer.observe) or both x and y")
        val (w, h) = bridge.screenSize()
        val (cx, cy) = Gestures.clampPoint(x, y, w, h)
        return Target(null, null, cx, cy)
    }

    private fun result(t: Target, via: String): Map<String, Any?> =
        mapOf("ok" to true, "via" to via, "target" to mapOf("id" to t.id, "x" to t.x.toInt(), "y" to t.y.toInt()))

    private suspend fun gesture(bridge: AccessibilityBridge, strokes: List<com.pai.plugin.api.GestureStroke>) {
        val done = withContext(Dispatchers.Main) { bridge.dispatchGesture(strokes) }
        if (!done) throw ControlError("gesture_failed", "Android did not perform the gesture (it was cancelled or refused, for example on a secure screen)")
    }

    // ------------------------------------------------------------------ click / long press

    private suspend fun click(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val button = (p["button"] as? String)?.lowercase() ?: "left"
        if (button != "left") throw ControlError("unsupported", "Android has no right or middle click: use computer.long_press for a context menu")
        val count = Gestures.intIn(p["count"], "count", 1, 1, 2)
        val t = target(bridge, p)
        log("click", t)
        if (t.node != null && count == 1 && performOnClickable(t.node.info, AccessibilityNodeInfo.ACTION_CLICK)) return result(t, "action")
        gesture(bridge, Gestures.tap(t.x, t.y, count))
        return result(t, "gesture")
    }

    private suspend fun longPress(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val t = target(bridge, p)
        log("long_press", t)
        if (t.node != null && performOnClickable(t.node.info, AccessibilityNodeInfo.ACTION_LONG_CLICK)) return result(t, "action")
        gesture(bridge, Gestures.longPress(t.x, t.y, Gestures.duration(p["duration_ms"], Gestures.DEFAULT_LONG_PRESS_MS, Gestures.MAX_LONG_PRESS_MS)))
        return result(t, "gesture")
    }

    /** The node, or the nearest ancestor that supports [action], performs it. */
    private fun performOnClickable(node: AccessibilityNodeInfo, action: Int): Boolean {
        var n: AccessibilityNodeInfo? = node
        var hops = 0
        while (n != null && hops < 6) {
            val supported = n.actionList.any { it.id == action }
            if (supported && n.performAction(action)) return true
            n = n.parent; hops++
        }
        return false
    }

    // ------------------------------------------------------------------ type / key

    private suspend fun type(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val text = TextEntry.text(p["text"])
        val clear = bool(p["clear_first"], false)
        val node: AccessNode = if (p["element_id"] != null) {
            target(bridge, p).node!!
        } else {
            bridge.activeRoot()?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let(::AccessNode)
                ?: throw ControlError("no_text_field", "No text field is focused. Tap one first (computer.click) or pass element_id")
        }
        if (!node.editable) throw ControlError("not_editable", "That element is not a text field")
        if (node.password) throw ControlError("password_field", "PAI does not type into password fields. Type it yourself on the phone")
        Log.i(TAG, "type into ${node.viewId ?: node.className} (${text.length} characters)")      // never the text itself
        if (!node.focused) { node.info.performAction(AccessibilityNodeInfo.ACTION_FOCUS); node.info.performAction(AccessibilityNodeInfo.ACTION_CLICK) }
        val updated = TextEntry.compose(node.info.text, text, clear, node.showingHint)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, updated) }
        if (!node.info.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            throw ControlError("action_failed", "The app did not accept the text (it may block automated typing)")
        }
        return mapOf("ok" to true, "typed_length" to text.length, "field_length" to updated.length)
    }

    private suspend fun key(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val k = Keys.parse(p["keys"])
        Log.i(TAG, "key $k")
        val ok = when (k) {
            GlobalKey.BACK -> bridge.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            GlobalKey.HOME -> bridge.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            GlobalKey.RECENTS -> bridge.performGlobalAction(AccessibilityService.GLOBAL_ACTION_RECENTS)
            GlobalKey.NOTIFICATIONS -> bridge.performGlobalAction(AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS)
            GlobalKey.QUICK_SETTINGS -> bridge.performGlobalAction(AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS)
            GlobalKey.ENTER -> focusedInput(bridge).let { n ->
                if (Build.VERSION.SDK_INT < 30) throw ControlError("unsupported", "Pressing Enter in a field needs Android 11 or newer: click the button instead")
                n.info.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            }
            GlobalKey.DELETE -> focusedInput(bridge).let { n ->
                if (n.password) throw ControlError("password_field", "PAI does not edit password fields")
                val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, TextEntry.deleteLast(n.info.text)) }
                n.info.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
        }
        if (!ok) throw ControlError("action_failed", "Android did not perform $k")
        return mapOf("ok" to true, "key" to k.name)
    }

    private fun focusedInput(bridge: AccessibilityBridge): AccessNode =
        bridge.activeRoot()?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let(::AccessNode)?.takeIf { it.editable }
            ?: throw ControlError("no_text_field", "No text field is focused")

    // ------------------------------------------------------------------ scroll / swipe

    private suspend fun scroll(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        val direction = (p["direction"] as? String)?.lowercase() ?: throw ControlError("bad_parameter", "direction is required")
        val amount = Gestures.intIn(p["amount"], "amount", 3, 1, 10)
        val (w, h) = bridge.screenSize()
        val hasTarget = p["element_id"] != null || (p["x"] != null && p["y"] != null)
        val t = if (hasTarget) target(bridge, p) else null
        val action = when (direction) {
            "down" -> AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_DOWN.id
            "up" -> AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_UP.id
            "left" -> AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_LEFT.id
            "right" -> AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_RIGHT.id
            else -> throw ControlError("bad_parameter", "direction must be up, down, left or right")
        }
        val region = t?.node?.bounds?.takeIf { it[2] > it[0] && it[3] > it[1] } ?: intArrayOf(0, 0, w, h)
        var steps = 0
        repeat(amount) {
            // Prefer the app's own scroll action (exact, no finger); fall back to a swipe.
            val done = t?.node?.let { performOnClickable(it.info, action) } == true
            if (!done) gesture(bridge, listOf(Gestures.scroll(direction, region)))
            steps++
            delay(SCROLL_SETTLE_MS)
        }
        return mapOf("ok" to true, "direction" to direction, "steps" to steps)
    }

    private suspend fun swipe(bridge: AccessibilityBridge, p: Map<String, Any?>): Map<String, Any?> {
        fun point(key: String): Pair<Float, Float> {
            val m = p[key] as? Map<*, *> ?: throw ControlError("bad_parameter", "$key must be {x, y}")
            val x = (m["x"] as? Number)?.toFloat(); val y = (m["y"] as? Number)?.toFloat()
            return if (x == null || y == null) throw ControlError("bad_parameter", "$key must be {x, y}") else x to y
        }
        val (w, h) = bridge.screenSize()
        val (fx, fy) = point("from").let { Gestures.clampPoint(it.first, it.second, w, h) }
        val (tx, ty) = point("to").let { Gestures.clampPoint(it.first, it.second, w, h) }
        Log.i(TAG, "swipe ($fx,$fy) -> ($tx,$ty)")
        gesture(bridge, Gestures.swipe(fx, fy, tx, ty, Gestures.duration(p["duration_ms"], Gestures.DEFAULT_SWIPE_MS, Gestures.MAX_SWIPE_MS)))
        return mapOf("ok" to true, "from" to mapOf("x" to fx.toInt(), "y" to fy.toInt()), "to" to mapOf("x" to tx.toInt(), "y" to ty.toInt()))
    }

    // ------------------------------------------------------------------ apps

    private fun launchable(c: Context): List<AppInfo> {
        val pm = c.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val infos = if (Build.VERSION.SDK_INT >= 33) pm.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(0))
                    else @Suppress("DEPRECATION") pm.queryIntentActivities(intent, 0)
        return infos.map { AppInfo(it.activityInfo.packageName, it.loadLabel(pm).toString()) }.distinctBy { it.packageName }
    }

    private fun label(c: Context, pkg: String): String =
        runCatching { c.packageManager.getApplicationLabel(c.packageManager.getApplicationInfo(pkg, 0)).toString() }.getOrDefault(pkg)

    private fun appsList(c: Context, p: Map<String, Any?>): Map<String, Any?> =
        mapOf("ok" to true, "apps" to Apps.filter(launchable(c), p["query"] as? String).map { mapOf("id" to it.packageName, "name" to it.label) })

    private fun appsLaunch(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val query = ((p["id"] ?: p["name"]) as? String)?.trim().orEmpty()
        if (query.isEmpty()) throw ControlError("bad_parameter", "name (or id) of the app is required")
        val app = Apps.match(query, launchable(c)) ?: throw ControlError("app_not_found", "No installed app matches \"$query\"")
        val intent = c.packageManager.getLaunchIntentForPackage(app.packageName) ?: throw ControlError("app_not_launchable", "${app.label} cannot be opened")
        Log.i(TAG, "launch ${app.packageName}")
        c.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return mapOf("ok" to true, "launched" to mapOf("id" to app.packageName, "name" to app.label))
    }

    private fun foreground(c: Context, bridge: AccessibilityBridge): Map<String, Any?> {
        val root = bridge.activeRoot()
        val pkg = root?.packageName?.toString() ?: bridge.activePackage()
            ?: throw ControlError("no_window", "No app window is readable right now")
        return mapOf("ok" to true, "id" to pkg, "name" to label(c, pkg), "title" to windowTitle(root))
    }

    // ------------------------------------------------------------------ helpers

    private fun log(what: String, t: Target) = Log.i(TAG, "$what element=${t.id ?: "-"} at (${t.x.toInt()},${t.y.toInt()})")
    private fun bool(v: Any?, default: Boolean) = when (v) { null -> default; is Boolean -> v; is String -> v.equals("true", true); else -> default }

    private fun failure(e: ControlError): Map<String, Any?> =
        linkedMapOf<String, Any?>("ok" to false, "error" to e.code, "message" to (e.message ?: e.code)).also { if (e.howToFix != null) it["how_to_fix"] = e.howToFix }

    override fun destroy() { ctx = null; last = null }

    private companion object {
        const val TAG = "PaiComputerKt"
        const val MAX_SHOT_SIDE = 1280
        const val SCROLL_SETTLE_MS = 250L
        const val HOW_TO_FIX = "Open Android Settings > Accessibility > PAI computer control (it may be under Installed or Downloaded apps) and turn it on. You can turn it off the same way at any time."
    }
}
