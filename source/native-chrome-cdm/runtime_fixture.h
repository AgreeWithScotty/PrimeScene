// Pinned genuine regular Chrome runtime; never use a system browser.
#pragma once
#include <string>
constexpr char kChromeRuntimeVersion[] = "158.0.8127.89";
constexpr DWORD kChromeVersionMS = 10354688u;
constexpr DWORD kChromeVersionLS = 532611161u;
inline std::wstring ChromeRuntimeExecutable(const std::wstring& root) { return root+L"\\runtime\\chrome.exe"; }
inline std::wstring ChromeRuntimeCdm(const std::wstring& root) { return root+L"\\runtime\\158.0.8127.89\\WidevineCdm\\_platform_specific\\win_x64\\widevinecdm.dll"; }
