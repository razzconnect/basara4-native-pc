/* Offline AppContent provider for the configured base-game profile and extracted add-ons. */
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define INVALID_ID ((int32_t)0x805a1000)
#define NOT_LOADED ((int32_t)0x805a1001)
#define PARAMETER ((int32_t)0x80d90002)
#define BUSY ((int32_t)0x80d90003)
#define MAX_ADDONS 64
typedef struct {
    char data[17];
    char padding[3];
    uint32_t status;
} AddcontInfo;
_Static_assert(sizeof(AddcontInfo)==24,"AppContent addcont info layout");
static unsigned refs, initialized, configured;
static uint32_t parameters[5];
static char addon_labels[MAX_ADDONS][17];
static unsigned addon_count;
static unsigned queries, lists;
void runtime_content_configure(const uint32_t values[5], const char (*labels)[17], unsigned count) {
    if (refs || initialized || configured || !values || (values[0]!=1 && values[0]!=3) ||
        count>MAX_ADDONS || (count && !labels)) {
        fputs("ERROR: invalid or late content profile\n",stderr); exit(1);
    }
    memcpy(parameters,values,sizeof(parameters));
    for (unsigned i=0;i<count;++i) {
        if (!memchr(labels[i],'\0',17) || strlen(labels[i])!=16) {
            fputs("ERROR: invalid DLC entitlement label in content profile\n",stderr); exit(1);
        }
        for (unsigned j=0;j<16;++j) {
            unsigned char c=(unsigned char)labels[i][j];
            if (!((c>='0' && c<='9') || (c>='A' && c<='Z') ||
                  (c>='a' && c<='z') || c=='_')) {
                fputs("ERROR: invalid DLC entitlement label in content profile\n",stderr); exit(1);
            }
        }
        memcpy(addon_labels[i],labels[i],17);
    }
    addon_count=count; configured=1;
}
int runtime_content_addon_index(uint32_t service_label, const char *label) {
    if (service_label || !label) return -1;
    for (unsigned i=0;i<addon_count;++i)
        if (!strncmp(addon_labels[i],label,17)) return (int)i;
    return -1;
}
static void require_provider(void) {
    if (!configured) { fputs("STOP: AppContent needs --content-profile\n",stderr); exit(21); }
}
/* System modules: every library this eboot imports is provided by the host
 * runtime, so loading one only tracks a reference count. AppContent (0xb4)
 * additionally requires the configured content profile. */
static unsigned module_refs[0x10000];
static ABI int32_t module_load(uint16_t id) {
    if (!id) return INVALID_ID;
    if (id==0xb4) require_provider();
    if (module_refs[id]==UINT32_MAX) { fputs("STOP: module reference overflow\n",stderr); exit(21); }
    ++module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; printf("Runtime: AppContent host provider loaded; references=%u\n",refs); }
    else printf("Runtime: system module 0x%x loaded (host implementation)\n",id);
    return 0;
}
static ABI int32_t module_loaded(uint16_t id) {
    if (!id) return INVALID_ID;
    return module_refs[id] ? 0 : NOT_LOADED;
}
static ABI int32_t module_unload(uint16_t id) {
    int32_t result=module_loaded(id);
    if (result) return result;
    --module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; if (!refs) initialized=0; }
    return 0;
}
static void require_loaded(void) {
    require_provider();
    if (!refs) { fputs("STOP: AppContent called before module load\n",stderr); exit(21); }
}
static ABI int32_t content_init(const unsigned char *init,unsigned char *boot) {
    require_loaded();
    if (!init || !boot) return PARAMETER;
    if (initialized) return BUSY;
    /* Only the reserved-zero init profile is implemented. */
    for (unsigned i=0;i<32;++i) if (init[i]) return PARAMETER;
    memset(boot,0,40); initialized=1;
    printf("Runtime: AppContent initialized; %u extracted add-on(s) available\n",addon_count);
    return 0;
}
static void require_initialized(void) {
    require_loaded();
    if (!initialized) { fputs("STOP: AppContent query before initialization\n",stderr); exit(21); }
}
static ABI int32_t param_int(uint32_t id,int32_t *out) {
    require_initialized();
    if (id>4 || !out) return PARAMETER;
    memcpy(out,&parameters[id],4); ++queries;
    printf("Runtime: AppContent parameter %u = %d\n",id,*out);
    return 0;
}
static ABI int32_t addon_list(uint32_t service,AddcontInfo *list,uint32_t capacity,uint32_t *hits) {
    require_initialized();
    if (service>7 || (capacity && !list) || (!capacity && !hits)) return PARAMETER;
    unsigned available=service==0 ? addon_count : 0;
    unsigned written=capacity<available ? capacity : available;
    for (unsigned i=0;i<written;++i) {
        memset(&list[i],0,sizeof(list[i]));
        memcpy(list[i].data,addon_labels[i],17);
        list[i].status=4;
    }
    if (hits) *hits=available;
    ++lists; return 0;
}
uintptr_t runtime_content_resolve(const char *name) {
    if (!strcmp(name,"g8cM39EUZ6o#libSceSysmodule")) return (uintptr_t)module_load;
    if (!strcmp(name,"fMP5NHUOaMk#libSceSysmodule")) return (uintptr_t)module_loaded;
    if (!strcmp(name,"eR2bZFAAU0Q#libSceSysmodule")) return (uintptr_t)module_unload;
    if (!strcmp(name,"R9lA82OraNs#libSceAppContent")) return (uintptr_t)content_init;
    if (!strcmp(name,"99b82IKXpH4#libSceAppContent")) return (uintptr_t)param_int;
    if (!strcmp(name,"xnd8BJzAxmk#libSceAppContent")) return (uintptr_t)addon_list;
    return 0;
}
void runtime_content_report(void) {
    printf("Runtime: AppContent references=%u, initialized=%u, parameter queries=%u, list queries=%u, add-ons=%u\n",
           refs,initialized,queries,lists,addon_count);
}
