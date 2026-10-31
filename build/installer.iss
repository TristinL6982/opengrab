; OpenGrab setup (M14). Build after PyInstaller:
;   iscc /DAppVersion=0.1.3 build\installer.iss
; Output: build\release\OpenGrab-<version>-setup.exe
;
; Layout: {app}\app\ (the program; the in-app updater swaps this folder),
; {app}\plugins\ and {app}\models\ for files shared by every user.
; User data (settings, queue, add-ons such as the OCR solver) lives in
; %APPDATA%\OpenGrab and is kept on uninstall unless the user asks.

#ifndef AppVersion
  #define AppVersion "0.1.3"
#endif

[Setup]
AppId={{6F0B7A52-1E43-4C8B-9B57-0E9C2C1F0A11}
AppName=OpenGrab
AppVersion={#AppVersion}
AppPublisher=OpenGrab contributors
AppPublisherURL=https://github.com/TristinL6982/opengrab
AppSupportURL=https://github.com/TristinL6982/opengrab/issues
AppUpdatesURL=https://github.com/TristinL6982/opengrab/releases
DefaultDirName={autopf}\OpenGrab
DefaultGroupName=OpenGrab
DisableProgramGroupPage=yes
OutputDir=release
OutputBaseFilename=OpenGrab-{#AppVersion}-setup
SetupIconFile=opengrab.ico
UninstallDisplayIcon={app}\app\OpenGrab.exe
UninstallDisplayName=OpenGrab
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
; ChineseSimplified.isl is vendored from jrsoftware/issrc (needs Inno Setup 6.5+)
Name: "zh"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
en.GroupShortcuts=Shortcuts:
en.GroupStart=Starting OpenGrab:
en.GroupLinks=Links and files OpenGrab opens:
en.GroupOcr=Captcha solving:
en.TaskAutostart=Start OpenGrab in the tray when I sign in to Windows
en.TaskMagnet=magnet: links
en.TaskOpenGrab=opengrab:// links (from web pages and the browser extension)
en.TaskTorrent=.torrent files
en.TaskCrawljob=.crawljob files
en.TaskDlc=.dlc files
en.TaskOcr=Download the small local OCR captcha solver (about 40 MB)
en.TaskOcrOn=Turn the OCR solver on by default
en.OcrStatus=Downloading the local OCR captcha solver...
en.OcrProgress=Captcha solver: %1 of %2 MB (%3%%)
en.OcrStarting=Captcha solver: starting the download...
en.OcrDone=Captcha solver installed.
en.OcrFailed=The captcha solver could not be downloaded (%1). You can install it later in Settings, Local OCR captcha solver.
en.KeepData=Also delete your OpenGrab settings, queue and downloaded add-ons in %APPDATA%\OpenGrab? Your downloaded files are not touched.
zh.GroupShortcuts=快捷方式:
zh.GroupStart=启动 OpenGrab:
zh.GroupLinks=由 OpenGrab 打开的链接和文件:
zh.GroupOcr=验证码识别:
zh.TaskAutostart=登录 Windows 时在托盘中启动 OpenGrab
zh.TaskMagnet=magnet: 链接
zh.TaskOpenGrab=opengrab:// 链接（来自网页和浏览器扩展）
zh.TaskTorrent=.torrent 文件
zh.TaskCrawljob=.crawljob 文件
zh.TaskDlc=.dlc 文件
zh.TaskOcr=下载小型本地 OCR 验证码识别组件（约 40 MB）
zh.TaskOcrOn=默认启用 OCR 识别
zh.OcrStatus=正在下载本地 OCR 验证码识别组件...
zh.OcrProgress=验证码识别组件：%1 / %2 MB（%3%%）
zh.OcrStarting=验证码识别组件：正在开始下载...
zh.OcrDone=验证码识别组件已安装。
zh.OcrFailed=无法下载验证码识别组件（%1）。之后可在“设置”的“本地 OCR 验证码识别”中安装。
zh.KeepData=同时删除 %APPDATA%\OpenGrab 中的 OpenGrab 设置、队列和已下载的附加组件吗？你下载的文件不会受影响。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:GroupShortcuts}"
Name: "autostart"; Description: "{cm:TaskAutostart}"; GroupDescription: "{cm:GroupStart}"; Flags: unchecked
Name: "magnet"; Description: "{cm:TaskMagnet}"; GroupDescription: "{cm:GroupLinks}"
Name: "opengrabproto"; Description: "{cm:TaskOpenGrab}"; GroupDescription: "{cm:GroupLinks}"
Name: "torrent"; Description: "{cm:TaskTorrent}"; GroupDescription: "{cm:GroupLinks}"
Name: "crawljob"; Description: "{cm:TaskCrawljob}"; GroupDescription: "{cm:GroupLinks}"
Name: "dlc"; Description: "{cm:TaskDlc}"; GroupDescription: "{cm:GroupLinks}"; Flags: unchecked
Name: "ocr"; Description: "{cm:TaskOcr}"; GroupDescription: "{cm:GroupOcr}"; Flags: unchecked
Name: "ocr\on"; Description: "{cm:TaskOcrOn}"; GroupDescription: "{cm:GroupOcr}"

[Dirs]
Name: "{app}\plugins"
Name: "{app}\models"

[Files]
Source: "release\OpenGrab\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OpenGrab"; Filename: "{app}\app\OpenGrab.exe"
Name: "{autodesktop}\OpenGrab"; Filename: "{app}\app\OpenGrab.exe"; Tasks: desktopicon

[Registry]
; Per-user (HKCU) like the in-app switches in Settings, Windows integration,
; so both can turn the same entries on and off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OpenGrab"; ValueData: """{app}\app\OpenGrab.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\magnet"; ValueType: string; ValueData: "URL:magnet protocol"; Tasks: magnet; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: magnet
Root: HKCU; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueData: """{app}\app\OpenGrab.exe"" ""%1"""; Tasks: magnet; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\opengrab"; ValueType: string; ValueData: "URL:opengrab protocol"; Tasks: opengrabproto; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\opengrab"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: opengrabproto
Root: HKCU; Subkey: "Software\Classes\opengrab\shell\open\command"; ValueType: string; ValueData: """{app}\app\OpenGrab.exe"" ""%1"""; Tasks: opengrabproto
Root: HKCU; Subkey: "Software\Classes\OpenGrab.Download"; ValueType: string; ValueData: "OpenGrab download"; Tasks: torrent crawljob dlc; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\OpenGrab.Download\shell\open\command"; ValueType: string; ValueData: """{app}\app\OpenGrab.exe"" ""%1"""; Tasks: torrent crawljob dlc
Root: HKCU; Subkey: "Software\Classes\.torrent"; ValueType: string; ValueData: "OpenGrab.Download"; Tasks: torrent; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "OpenGrab.Download"; ValueData: ""; Tasks: torrent; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.crawljob"; ValueType: string; ValueData: "OpenGrab.Download"; Tasks: crawljob; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.crawljob\OpenWithProgids"; ValueType: string; ValueName: "OpenGrab.Download"; ValueData: ""; Tasks: crawljob; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.dlc"; ValueType: string; ValueData: "OpenGrab.Download"; Tasks: dlc; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.dlc\OpenWithProgids"; ValueType: string; ValueName: "OpenGrab.Download"; ValueData: ""; Tasks: dlc; Flags: uninsdeletevalue

[Run]
; The OCR add-on is installed from [Code] (InstallOcr) so it can show its own progress bar.
Filename: "{app}\app\OpenGrab.exe"; Description: "{cm:LaunchProgram,OpenGrab}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; folders the in-app updater leaves next to app\
Type: filesandordirs; Name: "{app}\app.old"
Type: filesandordirs; Name: "{app}\app.new"
Type: filesandordirs; Name: "{app}\app.incoming"

[Code]
// ---- OCR add-on: a second progress bar under the main one ----------------
// OpenGrab downloads the add-on itself (pinned, SHA-256 checked wheels) as
// the signed-in user, and keeps a one-line progress file up to date:
//   "percent|done|total|step", then "done" or "error|message".
var
  OcrBar: TNewProgressBar;
  OcrLabel: TNewStaticText;

function GetTickCount: DWord; external 'GetTickCount@kernel32.dll stdcall';

// Keep the wizard responsive while waiting (otherwise Windows shows "Not Responding").
type
  TMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: Longint;
    lParam: Longint;
    time: DWORD;
    ptX: Longint;
    ptY: Longint;
  end;
function PeekMessage(var lpMsg: TMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: UINT): BOOL;
  external 'PeekMessageW@user32.dll stdcall';
function TranslateMessage(const lpMsg: TMsg): BOOL; external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const lpMsg: TMsg): Longint; external 'DispatchMessageW@user32.dll stdcall';

procedure PumpMessages;
var
  Msg: TMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, 1) do  // 1 = PM_REMOVE
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

procedure InitializeWizard;
var
  Gauge: TNewProgressBar;
begin
  Gauge := WizardForm.ProgressGauge;
  OcrLabel := TNewStaticText.Create(WizardForm);
  OcrLabel.Parent := WizardForm.InstallingPage;
  OcrLabel.Left := Gauge.Left;
  OcrLabel.Top := Gauge.Top + Gauge.Height + ScaleY(20);
  OcrLabel.AutoSize := False;
  OcrLabel.Width := Gauge.Width;
  OcrLabel.Height := ScaleY(16);
  OcrLabel.Visible := False;
  OcrBar := TNewProgressBar.Create(WizardForm);
  OcrBar.Parent := WizardForm.InstallingPage;
  OcrBar.Left := Gauge.Left;
  OcrBar.Top := OcrLabel.Top + OcrLabel.Height + ScaleY(4);
  OcrBar.Width := Gauge.Width;
  OcrBar.Height := Gauge.Height;
  OcrBar.Min := 0;
  OcrBar.Max := 100;
  OcrBar.Visible := False;
end;

// Field(S, 0) is the text before the first '|', Field(S, 1) the next one, ...
function Field(S: String; Index: Integer): String;
var
  I, P: Integer;
begin
  for I := 1 to Index do
  begin
    P := Pos('|', S);
    if P = 0 then
    begin
      Result := '';
      exit;
    end;
    Delete(S, 1, P);
  end;
  P := Pos('|', S);
  if P = 0 then Result := S else Result := Copy(S, 1, P - 1);
end;

procedure ShowOcr(Text: String);
begin
  OcrLabel.Caption := Text;
  WizardForm.InstallingPage.Repaint;
end;

procedure InstallOcr;
var
  ProgressFile, Params, Line, Err: String;
  Lines: AnsiString;
  Rc, Pct: Integer;
  Started: DWord;
  Finished: Boolean;
begin
  ProgressFile := ExpandConstant('{tmp}\ocr-progress.txt');
  DeleteFile(ProgressFile);
  Params := '--install-addon ocr --progress-file "' + ProgressFile + '"';
  if WizardIsTaskSelected('ocr\on') then
    Params := Params + ' --enable-ocr';
  WizardForm.StatusLabel.Caption := CustomMessage('OcrStatus');
  OcrLabel.Visible := True;
  OcrBar.Visible := True;
  OcrBar.Style := npbstMarquee;   // moving until the first progress arrives
  ShowOcr(CustomMessage('OcrStarting'));
  // runs as the signed-in user: the add-on belongs in their %APPDATA%
  if not ExecAsOriginalUser(ExpandConstant('{app}\app\OpenGrab.exe'), Params, '', SW_HIDE, ewNoWait, Rc) then
  begin
    OcrBar.Style := npbstNormal;
    OcrBar.State := npbsError;
    ShowOcr(FmtMessage(CustomMessage('OcrFailed'), [SysErrorMessage(Rc)]));
    exit;
  end;
  Started := GetTickCount;
  Finished := False;
  Err := '';
  while not Finished do
  begin
    PumpMessages;
    Sleep(100);
    if LoadStringFromFile(ProgressFile, Lines) then
    begin
      Line := Trim(String(Lines));
      if Line = 'done' then
        Finished := True
      else if Copy(Line, 1, 6) = 'error|' then
      begin
        Err := Copy(Line, 7, 200);
        Finished := True;
      end
      else if Line <> '' then
      begin
        Pct := StrToIntDef(Field(Line, 0), 0);
        OcrBar.Style := npbstNormal;
        OcrBar.Position := Pct;
        // (a line may not start with "[": Inno Setup would read it as a section)
        ShowOcr(FmtMessage(CustomMessage('OcrProgress'), [
          IntToStr(StrToInt64Def(Field(Line, 1), 0) div 1048576),
          IntToStr(StrToInt64Def(Field(Line, 2), 0) div 1048576), IntToStr(Pct)]));
      end;
    end;
    if GetTickCount - Started > 20 * 60 * 1000 then
    begin
      Err := 'timed out';
      Finished := True;
    end;
  end;
  OcrBar.Style := npbstNormal;
  if Err = '' then
  begin
    OcrBar.Position := 100;
    ShowOcr(CustomMessage('OcrDone'));
  end
  else
  begin
    OcrBar.State := npbsError;
    ShowOcr(FmtMessage(CustomMessage('OcrFailed'), [Err]));
    Log('OCR add-on: ' + Err);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('ocr') then
    InstallOcr;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{userappdata}\OpenGrab');
    if DirExists(Data) and (not UninstallSilent) then
      if MsgBox(CustomMessage('KeepData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
