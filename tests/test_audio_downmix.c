/* 7.1 to stereo fold (src/runtime_audio.c). */
#define _GNU_SOURCE
#include <assert.h>
#include <math.h>
#include "../src/runtime_audio.c"

uintptr_t runtime_lookup(const RuntimeExport *table, size_t count, const char *name) {
    (void)table; (void)count; (void)name;
    return 0;
}

static int near(float a, float b) { return fabsf(a-b)<1e-3f; }

/* One float frame in SDL order (FL FR C LFE BL BR SL SR) folded to stereo. */
static void fold_one(const float in[8], float out[2]) {
    unsigned char buf[8*4];
    memcpy(buf,in,sizeof(buf));
    assert(downmix_stereo(buf,1,1)==2*4);
    memcpy(out,buf,2*4);
}

int main(void) {
    float lr[2];
    /* Front channels pass at full level (SDL kept only ~0.21 of them). */
    fold_one((const float[8]){0.5f,0,0,0,0,0,0,0},lr); assert(near(lr[0],0.5f) && near(lr[1],0));
    fold_one((const float[8]){0,0.5f,0,0,0,0,0,0},lr); assert(near(lr[0],0) && near(lr[1],0.5f));
    /* Center and surrounds at -3 dB; LFE dropped. */
    fold_one((const float[8]){0,0,0.5f,0,0,0,0,0},lr); assert(near(lr[0],0.35355f) && near(lr[1],0.35355f));
    fold_one((const float[8]){0,0,0,1,0,0,0,0},lr); assert(near(lr[0],0) && near(lr[1],0));
    fold_one((const float[8]){0,0,0,0,0.5f,0,0.5f,0},lr); assert(near(lr[0],0.70711f) && near(lr[1],0));
    /* A loud mix is soft-limited (55ceee4): it approaches +-0.995 (0.7 + 0.295) instead of being
       clipped at full scale. */
    fold_one((const float[8]){1,-1,1,0,1,-1,1,-1},lr); assert(near(lr[0],0.995f) && near(lr[1],-0.99437f));
    /* The limiter on its own (one front channel, -8 .. 8): transparent up to 0.7, never past 0.995,
       monotonic and symmetric; just above the knee it is still continuous. */
    float previous=-1.0f;
    for (int i=-800;i<=800;++i) {
        const float x=(float)i/100.0f;
        float neg[2];
        fold_one((const float[8]){x,0,0,0,0,0,0,0},lr);
        fold_one((const float[8]){-x,0,0,0,0,0,0,0},neg);
        assert(fabsf(lr[0])<=0.995f+1e-6f && lr[1]==0);
        if (fabsf(x)<=0.7f) assert(lr[0]==x);
        assert(lr[0]>=previous && lr[0]==-neg[0]);
        previous=lr[0];
    }
    fold_one((const float[8]){0.7001f,0,0,0,0,0,0,0},lr); assert(near(lr[0],0.7001f));
    fold_one((const float[8]){0.9f,0,0,0,0,0,0,0},lr); assert(lr[0]>0.85f && lr[0]<0.9f);

    /* s16 frames, several in one buffer folded in place. */
    int16_t s[2*8]={16384,0,0,0,0,0,0,0, 32767,32767,32767,0,32767,32767,32767,32767};
    assert(downmix_stereo((unsigned char *)s,2,0)==2*2*2);
    assert(s[0]==16383 && s[1]==0);
    /* The loud s16 frame is limited below full scale too: (int16)(soft_limit * 32767), about 32603. */
    assert(s[2]==s[3] && s[2]>=32600 && s[2]<=(int16_t)(0.995f*32767.0f));
    puts("audio downmix ok");
    return 0;
}
