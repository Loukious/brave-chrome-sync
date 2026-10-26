// SPDX-License-Identifier: MPL-2.0
// Chromium guards this macro both before and after Windows headers.
#define StrCat StrCat
#include "sync_browser_registration.h"
#define StrCat StrCat

#include <iostream>
#include <stdexcept>

int wmain(int argc, wchar_t** argv) {
  if (argc != 4) {
    return 2;
  }
  const auto registration = brave_chrome_sync::Find(argv[2]);
  if (!registration || !brave_chrome_sync::EqualPath(registration->root, argv[1]) ||
      registration->name != argv[3] ||
      !brave_chrome_sync::HasProtocol(*registration, L"http") ||
      !brave_chrome_sync::HasProtocol(*registration, L"https")) {
    throw std::runtime_error("Native browser could not find its stable registration");
  }
  if (brave_chrome_sync::Find(L"C:\\not-installed\\versions\\missing\\brave.exe")) {
    throw std::runtime_error("Native browser mistook a foreign path for this installation");
  }
  std::cout << "Native Windows registration discovery and shell handler resolution passed\n";
  return 0;
}
