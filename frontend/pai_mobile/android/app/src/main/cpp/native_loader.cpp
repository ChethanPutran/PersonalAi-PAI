// android/app/src/main/cpp/native_loader.cpp
#include <jni.h>
#include <dlfcn.h>
#include <android/log.h>

#include <cstring>
#include <string>

#include "pai_plugin_abi.h"

#define LOG_TAG "PAI-Loader"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {

struct LoadedModule {
    void* dl = nullptr;
    pai_plugin_module_t* mod = nullptr;
    pai_plugin_destroy_fn destroy = nullptr;
};

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_pai_agent_plugins_DynamicLoader_nativeCreateModule(
    JNIEnv* env, jobject /*thiz*/, jstring path) {

    if (!path) return 0;
    const char* cpath = env->GetStringUTFChars(path, nullptr);
    if (!cpath) return 0;

    void* dl = dlopen(cpath, RTLD_NOW | RTLD_LOCAL);
    env->ReleaseStringUTFChars(path, cpath);

    if (!dl) {
        const char* e = dlerror();
        LOGE("dlopen failed: %s", e ? e : "unknown");
        return 0;
    }

    auto create = reinterpret_cast<pai_plugin_create_fn>(
        dlsym(dl, "pai_plugin_module_create"));
    auto destroy = reinterpret_cast<pai_plugin_destroy_fn>(
        dlsym(dl, "pai_plugin_module_destroy"));

    if (!create || !destroy) {
        LOGE("missing create/destroy symbols");
        dlclose(dl);
        return 0;
    }

    pai_plugin_module_t* mod = create();
    if (!mod) {
        LOGE("create returned null");
        dlclose(dl);
        return 0;
    }

    if (mod->abi_version != PAI_PLUGIN_ABI_VERSION) {
        LOGE("ABI mismatch: plugin=%d host=%d", mod->abi_version, PAI_PLUGIN_ABI_VERSION);
        destroy(mod);
        dlclose(dl);
        return 0;
    }

    auto* loaded = new LoadedModule{dl, mod, destroy};
    return reinterpret_cast<jlong>(loaded);
}

extern "C" JNIEXPORT void JNICALL
Java_com_pai_agent_plugins_DynamicLoader_nativeDestroyModule(
    JNIEnv*, jobject, jlong handle) {

    if (handle == 0) return;
    auto* loaded = reinterpret_cast<LoadedModule*>(handle);
    if (loaded->mod && loaded->destroy) loaded->destroy(loaded->mod);
    if (loaded->dl) dlclose(loaded->dl);
    delete loaded;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_pai_agent_plugins_CAbiPluginModule_nativeName(
    JNIEnv* env, jobject, jlong handle) {

    if (handle == 0) return env->NewStringUTF("");
    auto* loaded = reinterpret_cast<LoadedModule*>(handle);
    const char* n = loaded->mod->name();
    return env->NewStringUTF(n ? n : "");
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_com_pai_agent_plugins_CAbiPluginModule_nativeCapabilities(
    JNIEnv* env, jobject, jlong handle) {

    if (handle == 0) {
        return env->NewObjectArray(0, env->FindClass("java/lang/String"), nullptr);
    }
    auto* loaded = reinterpret_cast<LoadedModule*>(handle);
    size_t n = 0;
    const char** caps = loaded->mod->capabilities(&n);

    jclass stringClass = env->FindClass("java/lang/String");
    jobjectArray arr = env->NewObjectArray(static_cast<jsize>(n), stringClass, nullptr);
    for (size_t i = 0; i < n; ++i) {
        jstring s = env->NewStringUTF(caps[i] ? caps[i] : "");
        env->SetObjectArrayElement(arr, static_cast<jsize>(i), s);
        env->DeleteLocalRef(s);
    }
    return arr;
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_com_pai_agent_plugins_CAbiPluginModule_nativePermissions(
    JNIEnv* env, jobject, jlong handle) {

    if (handle == 0) {
        return env->NewObjectArray(0, env->FindClass("java/lang/String"), nullptr);
    }
    auto* loaded = reinterpret_cast<LoadedModule*>(handle);
    size_t n = 0;
    const char** ps = loaded->mod->permissions(&n);

    jclass stringClass = env->FindClass("java/lang/String");
    jobjectArray arr = env->NewObjectArray(static_cast<jsize>(n), stringClass, nullptr);
    for (size_t i = 0; i < n; ++i) {
        jstring s = env->NewStringUTF(ps[i] ? ps[i] : "");
        env->SetObjectArrayElement(arr, static_cast<jsize>(i), s);
        env->DeleteLocalRef(s);
    }
    return arr;
}

extern "C" JNIEXPORT jobject JNICALL
Java_com_pai_agent_plugins_CAbiPluginModule_nativeInvoke(
    JNIEnv* env, jobject, jlong handle, jstring capability, jobjectArray keys, jobjectArray values) {

    jclass mapClass = env->FindClass("java/util/HashMap");
    jmethodID mapCtor = env->GetMethodID(mapClass, "<init>", "()V");
    jobject resultMap = env->NewObject(mapClass, mapCtor);
    jmethodID put = env->GetMethodID(
        mapClass, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;");

    if (handle == 0) {
        env->CallObjectMethod(resultMap, put,
            env->NewStringUTF("ok"), env->NewStringUTF("false"));
        env->CallObjectMethod(resultMap, put,
            env->NewStringUTF("error"), env->NewStringUTF("module_not_loaded"));
        return resultMap;
    }

    auto* loaded = reinterpret_cast<LoadedModule*>(handle);

    // Build kv list from keys/values arrays
    jsize n = keys ? env->GetArrayLength(keys) : 0;
    std::vector<pai_kv_t> kv;
    std::vector<std::string> keyStr, valStr;
    kv.reserve(n);
    keyStr.reserve(n);
    valStr.reserve(n);
    for (jsize i = 0; i < n; ++i) {
        auto jk = (jstring)env->GetObjectArrayElement(keys, i);
        auto jv = (jstring)env->GetObjectArrayElement(values, i);
        const char* ck = jk ? env->GetStringUTFChars(jk, nullptr) : "";
        const char* cv = jv ? env->GetStringUTFChars(jv, nullptr) : "";
        keyStr.emplace_back(ck ? ck : "");
        valStr.emplace_back(cv ? cv : "");
        if (jk) env->ReleaseStringUTFChars(jk, ck);
        if (jv) env->ReleaseStringUTFChars(jv, cv);
        if (jk) env->DeleteLocalRef(jk);
        if (jv) env->DeleteLocalRef(jv);
    }
    for (jsize i = 0; i < n; ++i) {
        kv.push_back({keyStr[i].c_str(), valStr[i].c_str()});
    }
    pai_kv_list_t in{kv.data(), kv.size()};

    const char* ccap = capability ? env->GetStringUTFChars(capability, nullptr) : "";
    pai_kv_list_t out{};
    char* err = nullptr;
    int rc = loaded->mod->invoke(ccap ? ccap : "", &in, &out, &err);
    if (capability) env->ReleaseStringUTFChars(capability, ccap);

    if (rc != 0) {
        jstring jok = env->NewStringUTF("false");
        jstring jerr = env->NewStringUTF(err ? err : "unknown_error");
        env->CallObjectMethod(resultMap, put, env->NewStringUTF("ok"), jok);
        env->CallObjectMethod(resultMap, put, env->NewStringUTF("error"), jerr);
        env->DeleteLocalRef(jok);
        env->DeleteLocalRef(jerr);
        if (err && loaded->mod->free_string) loaded->mod->free_string(err);
        return resultMap;
    }

    for (size_t i = 0; i < out.count; ++i) {
        jstring k = env->NewStringUTF(out.items[i].key ? out.items[i].key : "");
        jstring v = env->NewStringUTF(out.items[i].value ? out.items[i].value : "");
        env->CallObjectMethod(resultMap, put, k, v);
        env->DeleteLocalRef(k);
        env->DeleteLocalRef(v);
    }
    if (loaded->mod->free_kv_list) loaded->mod->free_kv_list(&out);
    return resultMap;
}