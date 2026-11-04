/*
Copyright 2018 Embedded Microprocessor Benchmark Consortium (EEMBC)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Original Author: Shay Gal-on
*/
/* Topic : Description
        This file contains configuration constants required to execute on
   different platforms
*/
#ifndef CORE_PORTME_H
#define CORE_PORTME_H

/* size_t: 裸机下由编译器自带的 freestanding <stddef.h> 提供 (不依赖 newlib) */
#include <stddef.h>

/* 平台提供: 日志输出 (写 dmem 缓冲区, 见 core_portme.c) */
void uart_send_char(char c);
int  ee_printf(const char *fmt, ...);
/************************/
/* Data types and settings */
/************************/
/* Configuration : HAS_FLOAT
        Define to 1 if the platform supports floating point.
*/
#ifndef HAS_FLOAT
#define HAS_FLOAT 0
#endif
/* Configuration : HAS_TIME_H
        Define to 1 if platform has the time.h header file,
        and implementation of functions thereof.
*/
#ifndef HAS_TIME_H
#define HAS_TIME_H 0
#endif
/* Configuration : USE_CLOCK
        Define to 1 if platform has the time.h header file,
        and implementation of functions thereof.
*/
#ifndef USE_CLOCK
#define USE_CLOCK 0
#endif
/* Configuration : HAS_STDIO
        Define to 1 if the platform has stdio.h.
*/
#ifndef HAS_STDIO
#define HAS_STDIO 0
#endif
/* Configuration : HAS_PRINTF
        Define to 1 if the platform has stdio.h and implements the printf
   function.
*/
#ifndef HAS_PRINTF
#define HAS_PRINTF 0
#endif

/* Definitions : COMPILER_VERSION, COMPILER_FLAGS, MEM_LOCATION
        Initialize these strings per platform
*/
#ifndef COMPILER_VERSION
#ifdef __GNUC__
#define COMPILER_VERSION "GCC"__VERSION__
#else
#define COMPILER_VERSION "Please put compiler version here (e.g. gcc 4.1)"
#endif
#endif
#ifndef COMPILER_FLAGS
#define COMPILER_FLAGS \
    FLAGS_STR /* "Please put compiler flags here (e.g. -o3)" */
#endif
#ifndef MEM_LOCATION
#define MEM_LOCATION "STATIC"
#endif
/* ticks 就是 CPU 周期, 所以 CLOCKS_PER_SEC = 主频。
 *
 * 【W-02】主频只能有**一个来源**: 构建时传 `-DHW_CLK_HZ=70000000`, 它必须
 * 等于 `fpga/top_a100t.sv` 里顶层参数 `.CLK_HZ(...)` (XDC 的 sys_clk_70)。
 * 定义 HW_CLK_HZ 同时表示"这是上板/评分构建":
 *   ① CLOCKS_PER_SEC = 该频率 -> time_in_secs()/Iterations-per-Sec 才是真的
 *      (以前硬编码 50 MHz, 70 MHz 硬件上所有时间口径偏 1.4 倍: 3000 迭代真实
 *       17.70 s 会被算成 24.78 s, ">=10 s 才有效" 这条门也宽了 1.4 倍)
 *   ② CoreMark 自己的 ">=10 s 才算有效成绩" 判定生效 (CM_MIN_SECS_CHECK=1)
 * 仿真 tb_sw 确实是 50 MHz (sim/tb_sw.sv:22) 且只跑 1 次迭代 (约 8 ms),
 * 所以仿真/普通回归**不定义**该宏: CLOCKS_PER_SEC=50000000 且不做 >=10 s 判定
 * (仿真不是评分运行, 拿它当"有效成绩"反而是错的)。 */
#ifdef HW_CLK_HZ
#  define CLOCKS_PER_SEC HW_CLK_HZ
#  ifndef CM_MIN_SECS_CHECK
#    define CM_MIN_SECS_CHECK 1
#  endif
#else
#  ifndef CLOCKS_PER_SEC
#    define CLOCKS_PER_SEC 50000000
#  endif
#  ifndef CM_MIN_SECS_CHECK
#    define CM_MIN_SECS_CHECK 0
#  endif
#endif

/* Data Types :
        To avoid compiler issues, define the data types that need ot be used for
   8b, 16b and 32b in <core_portme.h>.

        *Imprtant* :
        ee_ptr_int needs to be the data type used to hold pointers, otherwise
   coremark may fail!!!
*/
typedef signed short   ee_s16;
typedef unsigned short ee_u16;
typedef signed int     ee_s32;
typedef double         ee_f32;
typedef unsigned char  ee_u8;
typedef unsigned int   ee_u32;
typedef ee_u32         ee_ptr_int;
typedef size_t         ee_size_t;
#define NULL ((void *)0)
/* align_mem :
        This macro is used to align an offset to point to a 32b value. It is
   used in the Matrix algorithm to initialize the input memory blocks.
*/
#define align_mem(x) (void *)(4 + (((ee_ptr_int)(x)-1) & ~3))

/* Configuration : CORE_TICKS
        Define type of return from the timing functions.
 */
/* 本平台的 MMIO 周期计数器是 64 位 (见 mmio_subsys.sv), 直接用 64 位 ticks。
 * 这很关键: 50 MHz 下 32 位约 86 秒溢出, 而赛题要求 CoreMark 至少跑 10 秒,
 * 64 位可以彻底不关心溢出。 */
#define CORETIMETYPE unsigned long long
typedef unsigned long long CORE_TICKS;

/* Configuration : SEED_METHOD
        Defines method to get seed values that cannot be computed at compile
   time.

        Valid values :
        SEED_ARG - from command line.
        SEED_FUNC - from a system function.
        SEED_VOLATILE - from volatile variables.
*/
#ifndef SEED_METHOD
#define SEED_METHOD SEED_VOLATILE
#endif

/* Configuration : MEM_METHOD
        Defines method to get a block of memry.

        Valid values :
        MEM_MALLOC - for platforms that implement malloc and have malloc.h.
        MEM_STATIC - to use a static memory array.
        MEM_STACK - to allocate the data block on the stack (NYI).
*/
#ifndef MEM_METHOD
#define MEM_METHOD MEM_STATIC
#endif

/* Configuration : MULTITHREAD
        Define for parallel execution

        Valid values :
        1 - only one context (default).
        N>1 - will execute N copies in parallel.

        Note :
        If this flag is defined to more then 1, an implementation for launching
   parallel contexts must be defined.

        Two sample implementations are provided. Use <USE_PTHREAD> or <USE_FORK>
   to enable them.

        It is valid to have a different implementation of <core_start_parallel>
   and <core_end_parallel> in <core_portme.c>, to fit a particular architecture.
*/
#ifndef MULTITHREAD
#define MULTITHREAD 1
#define USE_PTHREAD 0
#define USE_FORK    0
#define USE_SOCKET  0
#endif

/* Configuration : MAIN_HAS_NOARGC
        Needed if platform does not support getting arguments to main.

        Valid values :
        0 - argc/argv to main is supported
        1 - argc/argv to main is not supported

        Note :
        This flag only matters if MULTITHREAD has been defined to a value
   greater then 1.
*/
#ifndef MAIN_HAS_NOARGC
#define MAIN_HAS_NOARGC 1
#endif

/* Configuration : MAIN_HAS_NORETURN
        Needed if platform does not support returning a value from main.

        Valid values :
        0 - main returns an int, and return value will be 0.
        1 - platform does not support returning a value from main
*/
#ifndef MAIN_HAS_NORETURN
#define MAIN_HAS_NORETURN 0
#endif

/* Variable : default_num_contexts
        Not used for this simple port, must contain the value 1.
*/
extern ee_u32 default_num_contexts;

typedef struct CORE_PORTABLE_S
{
    ee_u8 portable_id;
} core_portable;

/* target specific init/fini */
void portable_init(core_portable *p, int *argc, char *argv[]);
void portable_fini(core_portable *p);

/* 【W-01】不返回: 把"算得对不对"编码进 tohost 后自旋 (见 core_portme.c)。
 * 以前 exit() 无论 code 是多少都写 tohost=1, 于是 CRC 不匹配也是绿灯。 */
void exit(int code);

#if !defined(PROFILE_RUN) && !defined(PERFORMANCE_RUN) \
    && !defined(VALIDATION_RUN)
#if (TOTAL_DATA_SIZE == 1200)
#define PROFILE_RUN 1
#elif (TOTAL_DATA_SIZE == 2000)
#define PERFORMANCE_RUN 1
#else
#define VALIDATION_RUN 1
#endif
#endif

int ee_printf(const char *fmt, ...);

#endif /* CORE_PORTME_H */
