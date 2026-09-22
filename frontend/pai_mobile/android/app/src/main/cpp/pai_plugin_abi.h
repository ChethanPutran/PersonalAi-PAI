// android/app/src/main/cpp/pai_plugin_abi.h
#ifndef PAI_PLUGIN_ABI_H
#define PAI_PLUGIN_ABI_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PAI_PLUGIN_ABI_VERSION 1

typedef struct {
    const char* key;
    const char* value;
} pai_kv_t;

typedef struct {
    const pai_kv_t* items;
    size_t count;
} pai_kv_list_t;

typedef struct pai_plugin_module {
    int abi_version;

    const char* (*name)(void);
    const char** (*capabilities)(size_t* out_count);
    const char** (*permissions)(size_t* out_count);

    int (*invoke)(
        const char* capability,
        const pai_kv_list_t* params,
        pai_kv_list_t* out_result,
        char** out_error);

    int (*request_permissions)(
        const char** permissions,
        size_t count,
        pai_kv_list_t* out_result,
        char** out_error);

    void (*free_kv_list)(pai_kv_list_t* list);
    void (*free_string)(char* s);
} pai_plugin_module_t;

typedef pai_plugin_module_t* (*pai_plugin_create_fn)(void);
typedef void                (*pai_plugin_destroy_fn)(pai_plugin_module_t*);

pai_plugin_module_t* pai_plugin_module_create(void);
void                 pai_plugin_module_destroy(pai_plugin_module_t*);

#ifdef __cplusplus
}
#endif
#endif