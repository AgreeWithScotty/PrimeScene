// Pinned genuine regular Chrome runtime; never use a system browser.
#pragma once
#include <string>
constexpr char kChromeRuntimeVersion[] = "158.0.8127.89";
constexpr DWORD kChromeVersionMS = 10354688u;
constexpr DWORD kChromeVersionLS = 532611161u;
// <addon_data>/native/chrome-cdm/<pin>/adapters/<hash>/{DLL,helper}.
// Adapter versions share a persistent runtime outside the add-on install tree.
inline std::wstring ChromeRuntimeDirectory(const std::wstring& root) {
  auto relative=root+L"\\..\\..\\runtime";wchar_t full[32768]={};
  DWORD size=GetFullPathNameW(relative.c_str(),32768,full,nullptr);
  return size&&size<32768?std::wstring(full,size):std::wstring();
}
inline std::wstring ChromeRuntimeExecutable(const std::wstring& root) { auto runtime=ChromeRuntimeDirectory(root);return runtime.empty()?std::wstring():runtime+L"\\chrome.exe"; }
inline std::wstring ChromeRuntimeCdm(const std::wstring& root) { auto runtime=ChromeRuntimeDirectory(root);return runtime.empty()?std::wstring():runtime+L"\\158.0.8127.89\\WidevineCdm\\_platform_specific\\win_x64\\widevinecdm.dll"; }
