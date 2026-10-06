int main(void){
  int fd=open("/dev/mem",O_RDONLY);
  if(fd<0){ printf("open fail\n"); return 1; }
  void*p=mmap(0,0x1000,PROT_READ,MAP_SHARED,fd,0x12180000);
  if(p==(void*)-1){ printf("mmap fail\n"); return 1; }
  volatile unsigned int*r=p;
  printf("ok 0x12180104=%08x 0x12180108=%08x\n", r[0x104/4], r[0x108/4]);
  return 0;
}
