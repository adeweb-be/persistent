/*****************************************************************************

  Copyright (c) 2012 Zope Foundation and Contributors.
  All Rights Reserved.

  This software is subject to the provisions of the Zope Public License,
  Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
  THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
  WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
  WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
  FOR A PARTICULAR PURPOSE

 ****************************************************************************/

#ifndef PERSISTENT__COMPAT_H
#define PERSISTENT__COMPAT_H

#include "Python.h"

#define INTERN PyUnicode_InternFromString
#define INTERN_INPLACE PyUnicode_InternInPlace
#define NATIVE_CHECK_EXACT PyUnicode_CheckExact
#define NATIVE_FROM_STRING_AND_SIZE PyUnicode_FromStringAndSize

#define Py_TPFLAGS_HAVE_RICHCOMPARE 0

#define INT_FROM_LONG(x) PyLong_FromLong(x)
#define INT_CHECK(x) PyLong_Check(x)
#define INT_AS_LONG(x) PyLong_AsLong(x)
#define CAPI_CAPSULE_NAME "persistent.cPersistence.CAPI"

/* PyPy only provides Py_TRASHCAN_SAFE_BEGIN/Py_TRASHCAN_SAFE_END. */
#ifndef Py_TRASHCAN_BEGIN
#define Py_TRASHCAN_BEGIN(op, dealloc) Py_TRASHCAN_SAFE_BEGIN(op)
#define Py_TRASHCAN_END Py_TRASHCAN_SAFE_END(NULL)
#endif

/* The free-threaded build of Python 3.14 and later runs the C extensions
   without the GIL. The pickle cache then needs PyUnstable_TryIncRef(),
   which is new in Python 3.14. The free-threaded build of Python 3.13
   keeps the GIL. */
#if defined(Py_GIL_DISABLED) && PY_VERSION_HEX >= 0x030E0000
#define PER_FREE_THREADING 1
#endif

#endif
