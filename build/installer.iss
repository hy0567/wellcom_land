; WellcomLAND 설치 스크립트 (Inno Setup)
; 빌드: ISCC.exe build/installer.iss
; 입력: dist/WellcomLAND/ (PyInstaller onedir 결과)
; 출력: dist/WellcomLAND_Setup.exe

#define MyAppName "WellcomLAND"
#define MyAppVersion "1.9.0"
#define MyAppPublisher "Wellcom"
#define MyAppExeName "WellcomLAND.exe"
#define TailscaleAuthKey "tskey-auth-kyaZwNxLUa11CNTRL-pFBEvigZ5m2REQRrSiE4m211EnJ4JxbJ"

[Setup]
AppId={{B5E7F3A2-1234-4C5D-9A8B-7E6F5D4C3B2A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\WellcomLAND
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=WellcomLAND_Setup
SetupIconFile=wellcom.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ★ 업그레이드(덮어쓰기) 허용
UsePreviousAppDir=yes
CloseApplications=force
RestartApplications=no

; 버전 표시 제거
ShowLanguageDialog=no
DisableWelcomePage=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
korean.WelcomeLabel1=WellcomLAND 설치
korean.WelcomeLabel2=WellcomLAND를 컴퓨터에 설치합니다.%n%n기존 버전이 있으면 자동으로 업그레이드됩니다.%n%n계속하려면 [다음]을 클릭하세요.
korean.FinishedHeadingLabel=WellcomLAND 설치 완료
korean.FinishedLabel=WellcomLAND가 성공적으로 설치되었습니다.

; 업데이트 시 기존 data/ 보존
[Files]
; EXE + _internal (런타임) — 항상 덮어쓰기
Source: "..\dist\WellcomLAND\WellcomLAND.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\WellcomLAND\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; app/ 코드 - .pyc 파일 — ★ 항상 덮어쓰기 (업그레이드 시에도 최신 코드 반영)
Source: "..\dist\WellcomLAND\_internal\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

; 네트워크 우선순위 수정 도구
Source: "fix_network_priority.bat"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "fix_network_priority.py"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "fix_network_priority.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\dist\tools\fix_network_priority.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

; Tailscale VPN 설치파일 (임시 폴더에 복사, 설치 후 삭제)
Source: "tailscale-setup.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall

; data/ 빈 폴더 생성 (기존 데이터 보존)
[Dirs]
Name: "{app}\data"; Permissions: everyone-full
Name: "{app}\logs"; Permissions: everyone-full
Name: "{app}\backup"; Permissions: everyone-full
Name: "{app}\app"; Permissions: everyone-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 생성"; GroupDescription: "추가 옵션:"

[Run]
; ★ Tailscale 필수 설치 (미설치 시만) — MSI 무인 설치
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\tailscale-setup.msi"" /quiet /norestart"; StatusMsg: "Tailscale VPN 설치 중..."; Flags: shellexec waituntilterminated; Check: not IsTailscaleInstalled
; Tailscale 서비스 시작 대기
Filename: "cmd.exe"; Parameters: "/c timeout /t 5 /nobreak >nul"; StatusMsg: "Tailscale 서비스 시작 대기..."; Flags: shellexec waituntilterminated runhidden
; ★ authkey로 회사 tailnet 자동 참여 + accept-routes + advertise-routes (LAN 서브넷 자동 감지)
Filename: "cmd.exe"; Parameters: "/c ""{code:GetTailscaleUpCmd}"""; StatusMsg: "Tailscale 네트워크 연결 중..."; Flags: shellexec waituntilterminated runhidden
; Tailscale 네트워크 인터페이스 메트릭 올리기 (LAN이 기본 라우트 유지)
Filename: "netsh.exe"; Parameters: "interface ipv4 set interface ""Tailscale"" metric=1000"; Flags: shellexec waituntilterminated runhidden
; WellcomLAND 실행
Filename: "{app}\{#MyAppExeName}"; Description: "WellcomLAND 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 업데이트로 생성된 파일 정리 (data/는 보존)
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\backup"
Type: filesandordirs; Name: "{app}\temp"

[Code]
// 설치 전: 실행 중인 WellcomLAND 종료 + 기존 버전 감지
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // taskkill로 실행 중인 프로세스 종료
  Exec('taskkill', '/f /im WellcomLAND.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // 잠시 대기 (파일 잠금 해제)
  Sleep(500);
end;

// 이전 버전 감지 시 안내
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
end;

// Tailscale 이미 설치 확인
function IsTailscaleInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf}\Tailscale\tailscale.exe'))
         or FileExists(ExpandConstant('{commonpf64}\Tailscale\tailscale.exe'));
end;

// LAN 서브넷 감지 (192.168.x.0/24, 10.x.x.0/24 등)
// Tailscale(100.x), APIPA(169.254.x), loopback(127.x) 제외
function GetLanSubnets(Param: String): String;
var
  ResultCode: Integer;
  TmpFile: String;
  Lines: TArrayOfString;
  I: Integer;
  Line, Subnet: String;
  Parts: TArrayOfString;
begin
  Result := '';
  TmpFile := ExpandConstant('{tmp}\lan_detect.txt');

  // ipconfig로 IPv4 주소 추출
  Exec('cmd.exe', '/c ipconfig | findstr /C:"IPv4" > "' + TmpFile + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if LoadStringsFromFile(TmpFile, Lines) then
  begin
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Line := Trim(Lines[I]);
      // "IPv4 주소 . . . . . . . . . : 192.168.0.198" 에서 IP 추출
      if Pos(':', Line) > 0 then
      begin
        Line := Trim(Copy(Line, Pos(':', Line) + 1, Length(Line)));
        // Tailscale(100.), APIPA(169.254.), loopback(127.) 제외
        if (Pos('100.', Line) <> 1) and (Pos('169.254.', Line) <> 1) and (Pos('127.', Line) <> 1) and (Pos('.', Line) > 0) then
        begin
          // IP를 서브넷으로 변환 (마지막 옥텟 → 0/24)
          Parts := SplitString(Line, '.');
          if GetArrayLength(Parts) = 4 then
          begin
            Subnet := Parts[0] + '.' + Parts[1] + '.' + Parts[2] + '.0/24';
            if Result = '' then
              Result := Subnet
            else if Pos(Subnet, Result) = 0 then
              Result := Result + ',' + Subnet;
          end;
        end;
      end;
    end;
  end;

  DeleteFile(TmpFile);

  // 서브넷이 없으면 빈 문자열
  Log('GetLanSubnets: ' + Result);
end;

// tailscale up 명령 생성 (서브넷이 있으면 --advertise-routes 포함)
function GetTailscaleUpCmd(Param: String): String;
var
  Subnets: String;
begin
  Subnets := GetLanSubnets('');
  if Subnets <> '' then
    Result := 'tailscale up --authkey={#TailscaleAuthKey} --accept-routes --advertise-routes=' + Subnets + ' --reset'
  else
    Result := 'tailscale up --authkey={#TailscaleAuthKey} --accept-routes --reset';
  Log('TailscaleUpCmd: ' + Result);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
end;

// 제거 시 data/ 보존 안내
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(ExpandConstant('{app}\data')) then
    begin
      if MsgBox('사용자 데이터(data/ 폴더)를 삭제하시겠습니까?' + #13#10 +
                '삭제하면 장치 설정과 데이터가 모두 사라집니다.',
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(ExpandConstant('{app}\data'), True, True, True);
        DelTree(ExpandConstant('{app}'), True, True, True);
      end;
    end;
  end;
end;
