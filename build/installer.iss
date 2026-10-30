; OpenGrab setup (M14). Build after PyInstaller:
;   iscc /DAppVersion=0.1.0 build\installer.iss
; Output: build\release\OpenGrab-<version>-setup.exe
;
; Layout: {app}\app\ (the program; the in-app updater swaps this folder),
; {app}\plugins\ and {app}\models\ for files shared by every user.
; User data (settings, queue, add-ons such as the OCR solver) lives in
; %APPDATA%\OpenGrab and is kept on uninstall unless the user asks.

#ifndef AppVersion
  #define AppVersion "0.1.0"
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
; The OCR add-on goes into the signed-in user's data folder, so it runs as
; that user, not as the administrator the installer may be running as.
Filename: "{app}\app\OpenGrab.exe"; Parameters: "--install-addon ocr --enable-ocr"; StatusMsg: "{cm:OcrStatus}"; Tasks: ocr\on; Flags: runasoriginaluser waituntilterminated runhidden
Filename: "{app}\app\OpenGrab.exe"; Parameters: "--install-addon ocr"; StatusMsg: "{cm:OcrStatus}"; Tasks: ocr and not ocr\on; Flags: runasoriginaluser waituntilterminated runhidden
Filename: "{app}\app\OpenGrab.exe"; Description: "{cm:LaunchProgram,OpenGrab}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; folders the in-app updater leaves next to app\
Type: filesandordirs; Name: "{app}\app.old"
Type: filesandordirs; Name: "{app}\app.new"
Type: filesandordirs; Name: "{app}\app.incoming"

[Code]
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
