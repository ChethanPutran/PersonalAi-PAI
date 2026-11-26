package com.pai.plugins.contacts

import android.content.ContentResolver
import android.provider.ContactsContract
import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Contacts (`pai.contacts`), READ-ONLY: find people by name or number. It cannot create, change or
 * delete contacts, and results are capped.
 */
class ContactsPlugin : PluginModule {

    override val name = "pai.contacts"
    override val capabilities = listOf("contacts.search", "contacts.get")
    override val permissions = listOf("contacts")

    private var resolver: ContentResolver? = null

    override fun attach(context: PluginContext) { resolver = context.appContext.contentResolver }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val cr = resolver ?: return failure("no_context_attached", "The contacts plugin is not ready")
        return try {
            withContext(Dispatchers.IO) {
                when (capability) {
                    "contacts.search" -> {
                        val q = ContactRules.query(params["query"]); val limit = ContactRules.limit(params["limit"])
                        mapOf("ok" to true, "contacts" to search(cr, q, limit))
                    }
                    "contacts.get" -> {
                        val id = (params["id"] as? String)?.trim().orEmpty().ifEmpty { throw ContactsError("bad_parameter", "id is required") }
                        val c = byId(cr, id) ?: throw ContactsError("not_found", "No such contact")
                        mapOf("ok" to true) + c
                    }
                    else -> failure("unknown_capability", "Unknown capability: $capability")
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ContactsError) {
            failure(e.code, e.message ?: e.code)
        } catch (e: SecurityException) {
            failure("permission_denied", "Contacts access has not been granted to PAI")
        } catch (t: Throwable) {
            Log.e(TAG, "invoke($capability) failed", t)
            failure("contacts_error", t.message ?: "Contacts failed")
        }
    }

    /** Contact ids whose name, number or email match, then every phone/email of those contacts. */
    private fun search(cr: ContentResolver, query: String, limit: Int): List<Map<String, Any?>> {
        val ids = LinkedHashSet<String>()
        val like = ContactRules.likePattern(query)
        cr.query(ContactsContract.Contacts.CONTENT_URI, arrayOf(ContactsContract.Contacts._ID),
            "${ContactsContract.Contacts.DISPLAY_NAME} LIKE ? ESCAPE '\\'", arrayOf(like),
            "${ContactsContract.Contacts.DISPLAY_NAME} COLLATE NOCASE ASC")?.use { c ->
            while (c.moveToNext() && ids.size < limit) ids += c.getString(0)
        }
        if (ContactRules.digits(query).length >= 3 && ids.size < limit) {
            cr.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, arrayOf(ContactsContract.CommonDataKinds.Phone.CONTACT_ID),
                "${ContactsContract.CommonDataKinds.Phone.NORMALIZED_NUMBER} LIKE ? OR ${ContactsContract.CommonDataKinds.Phone.NUMBER} LIKE ?",
                arrayOf("%" + ContactRules.digits(query) + "%", like), null)?.use { c ->
                while (c.moveToNext() && ids.size < limit) ids += c.getString(0)
            }
        }
        return ids.take(limit).mapNotNull { byId(cr, it) }
    }

    private fun byId(cr: ContentResolver, id: String): Map<String, Any?>? {
        val rows = ArrayList<Row>()
        var name = ""
        cr.query(ContactsContract.Contacts.CONTENT_URI, arrayOf(ContactsContract.Contacts.DISPLAY_NAME),
            "${ContactsContract.Contacts._ID} = ?", arrayOf(id), null)?.use { c -> if (c.moveToFirst()) name = c.getString(0).orEmpty() }
        if (name.isEmpty()) return null
        cr.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, arrayOf(ContactsContract.CommonDataKinds.Phone.NUMBER),
            "${ContactsContract.CommonDataKinds.Phone.CONTACT_ID} = ?", arrayOf(id), null)?.use { c ->
            while (c.moveToNext()) rows += Row(id, name, "phone", c.getString(0).orEmpty())
        }
        cr.query(ContactsContract.CommonDataKinds.Email.CONTENT_URI, arrayOf(ContactsContract.CommonDataKinds.Email.ADDRESS),
            "${ContactsContract.CommonDataKinds.Email.CONTACT_ID} = ?", arrayOf(id), null)?.use { c ->
            while (c.moveToNext()) rows += Row(id, name, "email", c.getString(0).orEmpty())
        }
        if (rows.isEmpty()) return mapOf("id" to id, "name" to name, "phones" to emptyList<String>(), "emails" to emptyList<String>())
        return ContactRules.group(rows, 1).first()
    }

    private fun failure(code: String, message: String) = mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() { resolver = null }

    private companion object { const val TAG = "PaiContactsKt" }
}
