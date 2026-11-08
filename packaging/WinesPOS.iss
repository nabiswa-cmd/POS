; Inno Setup 6 script. Build with packaging\build.ps1 (it runs PyInstaller first, which fills dist\WinesPOS).
#define AppName "Wines & Spirits POS"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B6D3A8F0-5C2E-4B1A-9E57-3F1D2A7C4E90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\WinesPOS
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=WinesPOS-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayName={#AppName}

[Files]
Source: "..\dist\WinesPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "post_install.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "pre_uninstall.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "verify_install.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "prereqs\*"; DestDir: "{app}\prereqs"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; The three screens. Till PCs only get the POS shortcut. Every shortcut runs the same program with a different first word.
Name: "{commondesktop}\POS (Cashier)"; Filename: "{app}\WinesPOS.exe"; Parameters: "pos"; WorkingDir: "{app}"
Name: "{commondesktop}\Manager"; Filename: "{app}\WinesPOS.exe"; Parameters: "manager"; WorkingDir: "{app}"; Check: IsServer
Name: "{commondesktop}\Stock Receiving"; Filename: "{app}\WinesPOS.exe"; Parameters: "receiving"; WorkingDir: "{app}"; Check: IsServer
Name: "{group}\POS (Cashier)"; Filename: "{app}\WinesPOS.exe"; Parameters: "pos"; WorkingDir: "{app}"
Name: "{group}\Manager"; Filename: "{app}\WinesPOS.exe"; Parameters: "manager"; WorkingDir: "{app}"; Check: IsServer
Name: "{group}\Stock Receiving"; Filename: "{app}\WinesPOS.exe"; Parameters: "receiving"; WorkingDir: "{app}"; Check: IsServer

[UninstallRun]
; Removes the server and backup tasks. The shop's data in C:\ProgramData\WinesPOS is deliberately left alone.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\pre_uninstall.ps1"""; Flags: runhidden; RunOnceId: "RemoveWinesPOSTasks"

[Code]
var
  RolePage: TInputOptionWizardPage;
  ShopPage: TInputQueryWizardPage;
  AccountPage: TInputQueryWizardPage;
  TillPage: TInputQueryWizardPage;

function IsServer: Boolean;
begin
  Result := RolePage.SelectedValueIndex = 0;
end;

procedure InitializeWizard;
begin
  RolePage := CreateInputOptionPage(wpSelectDir, 'What is this PC?', 'Choose the job of this computer',
    'Every shop has ONE server PC. A second till, if you have one, is a Till PC.', True, False);
  RolePage.Add('Shop server PC (holds the data; also used as Till 1)');
  RolePage.Add('Till PC (connects to the server PC over the shop network)');
  RolePage.SelectedValueIndex := 0;

  ShopPage := CreateInputQueryPage(RolePage.ID, 'Shop details', 'Server PC',
    'Leave the network address empty if the shop has only one till.');
  ShopPage.Add('Shop name:', False);
  ShopPage.Add('Manager login name (for example: owner):', False);
  ShopPage.Add('This PC''s address on the shop network (only if a second till will connect), for example 192.168.1.10:', False);

  AccountPage := CreateInputQueryPage(ShopPage.ID, 'Manager password', 'Server PC',
    'The manager password is not stored anywhere readable. Write it down and keep it safe.');
  AccountPage.Add('Manager password (at least 10 characters):', True);
  AccountPage.Add('Type it again:', True);
  AccountPage.Add('PostgreSQL "postgres" password (leave empty if setup should install PostgreSQL itself):', True);

  TillPage := CreateInputQueryPage(RolePage.ID, 'Server address', 'Till PC',
    'Type the address of the server PC, for example http://192.168.1.10:8000');
  TillPage.Add('Server address:', False);
  TillPage.Values[0] := 'http://';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = ShopPage.ID) or (PageID = AccountPage.ID) then
    Result := not IsServer;
  if PageID = TillPage.ID then
    Result := IsServer;
end;

function HasQuote(S: String): Boolean;
begin
  Result := Pos('"', S) > 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ShopPage.ID then
  begin
    if (Trim(ShopPage.Values[0]) = '') or (Trim(ShopPage.Values[1]) = '') then
    begin
      MsgBox('Please enter the shop name and the manager login name.', mbError, MB_OK);
      Result := False;
    end
    else if HasQuote(ShopPage.Values[0]) or HasQuote(ShopPage.Values[1]) or HasQuote(ShopPage.Values[2]) then
    begin
      MsgBox('Please do not use the " character.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = AccountPage.ID then
  begin
    if Length(AccountPage.Values[0]) < 10 then
    begin
      MsgBox('The manager password must be at least 10 characters.', mbError, MB_OK);
      Result := False;
    end
    else if AccountPage.Values[0] <> AccountPage.Values[1] then
    begin
      MsgBox('The two passwords are not the same.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = TillPage.ID then
  begin
    if (Pos('http://', TillPage.Values[0]) <> 1) or (Length(TillPage.Values[0]) < 12) or HasQuote(TillPage.Values[0]) then
    begin
      MsgBox('Type the full address, starting with http://, for example http://192.168.1.10:8000', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args, SecretsPath: String;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  WizardForm.StatusLabel.Caption := 'Setting up the shop. This can take several minutes...';
  SecretsPath := ExpandConstant('{tmp}\wp-secrets.txt');
  Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\post_install.ps1') +
          '" -AppDir "' + ExpandConstant('{app}') + '"';
  if IsServer then
  begin
    SaveStringToFile(SecretsPath, AccountPage.Values[0] + #13#10 + AccountPage.Values[2] + #13#10, False);
    Args := Args + ' -Role server -ShopName "' + Trim(ShopPage.Values[0]) + '" -ManagerUser "' + Trim(ShopPage.Values[1]) +
            '" -SecretsFile "' + SecretsPath + '"';
    if Trim(ShopPage.Values[2]) <> '' then
      Args := Args + ' -LanHost "' + Trim(ShopPage.Values[2]) + '"';
  end
  else
    Args := Args + ' -Role till -ServerUrl "' + Trim(TillPage.Values[0]) + '"';
  if (not Exec('powershell.exe', Args, '', SW_HIDE, ewWaitUntilTerminated, Code)) or (Code <> 0) then
    MsgBox('The program was copied, but this PC could not be set up.' + #13#10 +
           'Details: C:\ProgramData\WinesPOS\logs\install.log' + #13#10 +
           'Fix the problem, then run this installer again: it is safe to run over an existing install.', mbError, MB_OK);
  DeleteFile(SecretsPath);
end;
