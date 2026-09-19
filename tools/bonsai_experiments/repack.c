#include <stdint.h>
#include <stddef.h>
#include <string.h>
// Integer-only representation change. Never rounds or changes a scale.
int repack(const uint8_t *src, uint8_t *dst, size_t blocks) {
  for(size_t b=0;b<blocks;b++,src+=34,dst+=28) {
    uint8_t q[128];
    if ((((unsigned)src[1]<<8 | src[0]) & 0x7c00)==0x7c00) return 2;
    for(int i=0;i<128;i++) {q[i]=(src[2+i/4]>>(2*(i%4)))&3; if(q[i]==3) return 3;}
    int at=0,j=0;
    for(int c=16;c>=8;c/=2) {
      for(int m=0;m<c;m++) {
        unsigned v=0; for(int n=0;n<5;n++) v=3*v+q[at+m+n*c];
        dst[j+m]=(v*256+242)/243;
      }
      at+=5*c;j+=c;
    }
    for(int h=0;h<2;h++) {
      unsigned v=0;for(int n=0;n<4;n++) v=3*v+q[120+h+n*2];
      dst[24+h]=(v*3*256+242)/243;
    }
    memcpy(dst+26,src,2);
  }
  return 0;
}
// Independent decoding order from pinned Prism dequantize_row_ptq1_0.
int verify(const uint8_t *src, const uint8_t *dst, size_t blocks) {
  const uint8_t pow3[6]={1,3,9,27,81,243};
  const size_t stages[3]={32,16,8};
  for(size_t b=0;b<blocks;b++,src+=34,dst+=28) {
    if(memcmp(src,dst+26,2))return 1;
    size_t j=0,i=0;
    for(size_t s=0;s<3;s++) {
      size_t c=stages[s];
      for(;j+c<=24;j+=c)for(size_t n=0;n<5;n++)for(size_t m=0;m<c;m++) {
        uint8_t q=dst[j+m]*pow3[n];unsigned code=((uint16_t)q*3)>>8;
        if(code!=((src[2+i/4]>>(2*(i%4)))&3))return 2;i++;
      }
    }
    for(size_t n=0;n<4;n++)for(size_t h=0;h<2;h++) {
      uint8_t q=dst[24+h]*pow3[n];unsigned code=((uint16_t)q*3)>>8;
      if(code!=((src[2+i/4]>>(2*(i%4)))&3))return 3;i++;
    }
    if(i!=128)return 4;
  }
  return 0;
}
