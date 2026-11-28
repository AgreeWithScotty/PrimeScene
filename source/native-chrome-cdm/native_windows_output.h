// Measured Windows output protection for the native CDM proxy.
#pragma once
#include <roapi.h>
#include <atomic>
#include <vector>
#include <windows.media.protection.h>
#include <wrl.h>
#include <wrl/wrappers/corewrappers.h>
class WindowsOutput {
  using Protection=ABI::Windows::Media::Protection::HdcpProtection;
  using Result=ABI::Windows::Media::Protection::HdcpSetProtectionResult;
  Microsoft::WRL::ComPtr<ABI::Windows::Media::Protection::IHdcpSession> session;
  bool apartment_owned=false;
  bool automatic_probe=true;
  unsigned requested_level=UINT_MAX;
  struct Pending {
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncOperation<Result>> operation;
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncInfo> info;
  };
  std::vector<Pending> pending;
  bool Established(unsigned level) {
    effective_level=UINT_MAX;
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IReference<Protection>> effective;
    last=session->GetEffectiveProtection(&effective);
    if(FAILED(last)||!effective)return false;
    Protection value;last=effective->get_Value(&value);
    if(FAILED(last))return false;
    effective_level=static_cast<unsigned>(value);
    if(effective_level!=level)return false;
    boolean active=false;last=session->IsEffectiveProtectionAtLeast(static_cast<Protection>(level),&active);
    return SUCCEEDED(last)&&active;
  }
 public:
  explicit WindowsOutput(bool probe=true):automatic_probe(probe){}
  HRESULT last=E_FAIL;
  unsigned async_status=0,state_checks=0,protection_result=UINT_MAX,effective_level=UINT_MAX;
  bool verified_while_pending=false;
  bool Start(const std::atomic<bool>* cancelled=nullptr) {
    last=RoInitialize(RO_INIT_MULTITHREADED);apartment_owned=SUCCEEDED(last);
    if(FAILED(last)&&last!=RPC_E_CHANGED_MODE)return false;
    Microsoft::WRL::ComPtr<IInspectable> instance;
    last=RoActivateInstance(Microsoft::WRL::Wrappers::HStringReference(L"Windows.Media.Protection.HdcpSession").Get(),&instance);
    if(FAILED(last))return false;
    last=instance.As(&session);if(FAILED(last))return false;
    return Request(2,cancelled);
  }
  bool Request(unsigned level,const std::atomic<bool>* cancelled=nullptr) {
    if(!session)return false;
    async_status=0;state_checks=0;verified_while_pending=false;protection_result=UINT_MAX;effective_level=UINT_MAX;
    if(cancelled&&cancelled->load()){last=HRESULT_FROM_WIN32(ERROR_CANCELLED);return false;}
    if(requested_level==level&&Established(level))return true;
    if(requested_level!=UINT_MAX&&requested_level!=level){
      for(auto& item:pending)item.info->Cancel();pending.clear();
    }
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncOperation<Result>> operation;
    last=session->SetDesiredMinProtectionAsync(static_cast<Protection>(level),&operation);
    if(FAILED(last))return false;
    Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IAsyncInfo> info;
    last=operation.As(&info);if(FAILED(last))return false;
    ABI::Windows::Foundation::AsyncStatus status=ABI::Windows::Foundation::AsyncStatus::Started;
    ULONGLONG limit=GetTickCount64()+8000;
    while(GetTickCount64()<limit) {
      if(cancelled&&cancelled->load()){info->Cancel();last=HRESULT_FROM_WIN32(ERROR_CANCELLED);return false;}
      last=info->get_Status(&status);async_status=static_cast<unsigned>(status);
      if(FAILED(last))return false;
      if(status!=ABI::Windows::Foundation::AsyncStatus::Started)break;
      ++state_checks;
      if(Established(level)) {
        // Actual WinRT protection, never a fabricated CDM/HDCP capability.
        // Retain a still-pending request so releasing it cannot undo protection.
        pending.push_back({operation,info});requested_level=level;
        verified_while_pending=true;last=S_OK;return true;
      }
      Sleep(20);
    }
    if(status!=ABI::Windows::Foundation::AsyncStatus::Completed){
      if(status==ABI::Windows::Foundation::AsyncStatus::Started){info->Cancel();last=HRESULT_FROM_WIN32(ERROR_TIMEOUT);}
      else {HRESULT error=E_FAIL;info->get_ErrorCode(&error);info->Close();last=FAILED(error)?error:E_FAIL;}
      return false;
    }
    Result result;last=operation->GetResults(&result);info->Close();
    if(SUCCEEDED(last))protection_result=static_cast<unsigned>(result);
    bool success=SUCCEEDED(last)&&result==ABI::Windows::Media::Protection::HdcpSetProtectionResult_Success&&Established(level);
    if(!success&&SUCCEEDED(last))last=E_FAIL;
    if(success)requested_level=level;
    return success;
  }
  prime_wire::Bytes Query(bool change,uint32_t desired,const std::atomic<bool>* cancelled=nullptr) {
    prime_wire::Bytes data(12,0);
    if(change&&desired>1){last=E_INVALIDARG;return data;}
    if(change&&!desired&&!session){last=S_OK;prime_wire::Put32(data,0,1);return data;}
    // QueryStatus is observational. A prior failed setup does not authorize
    // another intrusive HDCP request. Return unknown, as the timed-out path
    // did, until the genuine CDM explicitly requests output protection.
    if(!session&&!change&&!automatic_probe){last=HRESULT_FROM_WIN32(ERROR_NOT_READY);return data;}
    if(!session&&!Start(cancelled))return data;
    if(change) {
      if(desired>1)return data;
      if(!Request(desired?2:0,cancelled))return data;
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
  void Close() {
    for(auto& item:pending){ABI::Windows::Foundation::AsyncStatus status;
      if(SUCCEEDED(item.info->get_Status(&status))&&status==ABI::Windows::Foundation::AsyncStatus::Started)item.info->Cancel();}
    if(session){Microsoft::WRL::ComPtr<ABI::Windows::Foundation::IClosable> closable;if(SUCCEEDED(session.As(&closable)))closable->Close();session.Reset();}
    for(auto& item:pending){ABI::Windows::Foundation::AsyncStatus status;
      if(SUCCEEDED(item.info->get_Status(&status))&&status!=ABI::Windows::Foundation::AsyncStatus::Started)item.info->Close();}
    pending.clear();requested_level=UINT_MAX;
    if(apartment_owned){RoUninitialize();apartment_owned=false;}
  }
  ~WindowsOutput(){Close();}
};
