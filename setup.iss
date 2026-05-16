; ============================================================
;  LangAgent 安装脚本 (Inno Setup 6)
;  编译: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
; ============================================================

#define AppName "LangAgent"
#define AppVersion "1.1.1"
#define AppPublisher "LangAgent"
#define AppExeName "LangAgent.exe"

[Setup]
AppId={{B8F4A3D2-7E6C-4A1F-9D5B-2C8E0F6A7D3E}}
AppName={#AppName}
AppVerName={#AppName} {#AppVersion}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
ArchitecturesInstallIn64BitMode=x64compatible
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=LangAgent_Setup_v{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
DirExistsWarning=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"

[Files]
; 主程序
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion restartreplace uninsrestartdelete
; 图标
Source: "app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
; 运行时资源（PyInstaller 已打包进 exe，此处为备用）
Source: "index.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "wechat_agent.py"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function _killProcess(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/f /im LangAgent.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Result := True;
end;

function _compareVersion(v1, v2: String): Integer;
var
  i, n1, n2: Integer;
  p1, p2: String;
begin
  // 返回: -1=v1<v2, 0=v1==v2, 1=v1>v2
  p1 := v1; p2 := v2;
  while (p1 <> '') or (p2 <> '') do
  begin
    i := Pos('.', p1);
    if i > 0 then begin n1 := StrToIntDef(Copy(p1, 1, i-1), 0); Delete(p1, 1, i); end
    else begin n1 := StrToIntDef(p1, 0); p1 := ''; end;
    i := Pos('.', p2);
    if i > 0 then begin n2 := StrToIntDef(Copy(p2, 1, i-1), 0); Delete(p2, 1, i); end
    else begin n2 := StrToIntDef(p2, 0); p2 := ''; end;
    if n1 < n2 then begin Result := -1; Exit; end;
    if n1 > n2 then begin Result := 1; Exit; end;
  end;
  Result := 0;
end;

function InitializeSetup(): Boolean;
var
  InstalledVer: String;
begin
  Result := True;
  _killProcess();

  // 阻止高版本被低版本覆盖
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
      '{B8F4A3D2-7E6C-4A1F-9D5B-2C8E0F6A7D3E}_is1', 'DisplayVersion', InstalledVer) then
  begin
    if _compareVersion(InstalledVer, '{#AppVersion}') > 0 then
    begin
      MsgBox('已安装的版本 (v' + InstalledVer + ') 高于当前安装包 (v{#AppVersion})。' + #13#10 +
             '请使用 v' + InstalledVer + ' 或更高版本的安装包。',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := _killProcess();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Exec('cmd.exe', '/c rmdir /s /q "' + ExpandConstant('{userappdata}\LangAgent') + '"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
