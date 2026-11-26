// Measured Windows output protection for the native CDM proxy.
#pragma once
#include <roapi.h>
#include <windows.media.protection.h>
#include <wrl.h>
#include <wrl/wrappers/corewrappers.h>
class WindowsOutput {
  using Protection=ABI::Windows::Media::Protection::HdcpProtection;
  using Result=ABI::Windows::Media::Protection::HdcpSetProtectionResult;
  Microsoft::WRL::ComPtr<ABI::Windows::Media::Protection::IHdcpSession> session;
  bool apartment_owned=false;
 public:
  HRESULT last=E_FAIL;
  bool Start() {
    last=RoInitialize(RO_INIT_MULTITHREADED);apartment_owned=SUCCEEDED(last);
    if(FAILED(last)&&last!=RPC_E_CHANGED_MODE)return false;
    Microsoft::WRL::ComPtr<IInspectable> instance;
    last=RoActivateInstance(Microsoft::WRL::Wrappers::HStringReference(L"Windows.Media.Protection.HdcpSession").Get(),&instance);
    if(FAILED(last))return false;
    last=instance.As(&session);if(FAILED(last))return false;
    return Request(2);
  }
  bool Request(unsigned level) {
    if(!session)return false;
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncOperation<Result>> operation;
    last=session->SetDesiredMinProtectionAsync(static_cast<Protection>(level),&operation);
    if(FAILED(last))return false;
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncInfo> info;
    last=operation.As(&info);if(FAILED(last))return false;
    ABI::Windows::Foundation::AsyncStatus status=ABI::Windows::Foundation::AsyncStatus::Started;
    ULONGLONG limit=GetTickCount64()+8000;
    while(GetTickCount64()<limit) {
      last=info->get_Status(&status);if(FAILED(last)||status!=ABI::Windows::Foundation::AsyncStatus::Started)break;
      Sleep(20);
    }
    if(status!=ABI::Windows::Foundation::AsyncStatus::Completed){info->Cancel();info->Close();last=HRESULT_FROM_WIN32(ERROR_TIMEOUT);return false;}
    Result result;last=operation->GetResults(&result);info->Close();
    return SUCCEEDED(last)&&result==ABI::Windows::Media::Protection::HdcpSetProtectionResult_Success;
  }
  prime_wire::Bytes Query(bool change,uint32_t desired) {
    prime_wire::Bytes data(12,0);
    if(!session&&!Start())return data;
    if(change) {
      if(desired>1)return data;
      if(!Request(desired?2:0))return data;
    }
    boolean on=false;
    last=session->IsEffectiveProtectionAtLeast(ABI::Windows::Media::Protection::HdcpProtection_On,&on);
    if(FAILED(last))return data;
    UINT32 paths=0,modes=0;
    LONG rc=GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS,&paths,&modes);
    if(rc||!paths||paths>64||modes>256){last=HRESULT_FROM_WIN32(rc?rc:ERROR_INVALID_DATA);return data;}
    std::vector<DISPLAYCONFIG_PATH_INFO> path_data(paths);
    std::vector<DISPLAYCONFIG_MODE_INFO> mode_data(modes);
    rc=QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS,&paths,path_data.data(),&modes,mode_data.data(),nullptr);
    if(rc){last=HRESULT_FROM_WIN32(rc);return data;}
    uint32_t links=0;
    for(UINT32 i=0;i<paths;++i) {
      switch(path_data[i].targetInfo.outputTechnology) {
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_HD15:links|=4;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_DVI:links|=16;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_HDMI:links|=8;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_DISPLAYPORT_EXTERNAL:links|=32;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_LVDS:
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_DISPLAYPORT_EMBEDDED:
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_UDI_EMBEDDED:
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_INTERNAL:links|=2;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_UDI_EXTERNAL:links|=32;break;
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_MIRACAST:
        case DISPLAYCONFIG_OUTPUT_TECHNOLOGY_INDIRECT_VIRTUAL:links|=64;break;
        default:links|=1;break;
      }
    }
    prime_wire::Put32(data,0,1);prime_wire::Put32(data,4,links);prime_wire::Put32(data,8,on?1:0);
    last=S_OK;return data;
  }
  ~WindowsOutput() {
    if(session){Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IClosable> closable;if(SUCCEEDED(session.As(&closable)))closable->Close();session.Reset();}
    if(apartment_owned)RoUninitialize();
  }
};
