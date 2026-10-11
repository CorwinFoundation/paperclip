param([ValidateSet('Prepare','Publish')][string]$Mode='Prepare')
$ErrorActionPreference='Stop'
$siteId='cf1f19cf-e13b-483e-a7f7-daadbc5bcbaf'
$config=Get-Content -Raw "$env:APPDATA/netlify/Config/config.json" | ConvertFrom-Json
$token=$config.users.PSObject.Properties[$config.userId].Value.auth.token
$headers=@{Authorization='Bearer '+$token}; $api='https://api.netlify.com/api/v1'
$site=Invoke-RestMethod "$api/sites/$siteId" -Headers $headers
if($site.name -ne 'backbond-holding' -or $site.custom_domain -ne 'backbond.ai'){throw 'Unexpected site/domain'}
& node --check (Join-Path $PSScriptRoot 'scan/report.js'); if($LASTEXITCODE -ne 0){throw 'Syntax check failed'}
& node (Join-Path $PSScriptRoot 'verify.js'); if($LASTEXITCODE -ne 0){throw 'Regression checks failed'}
$overlay=@{}
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'scan') -Recurse -File | ForEach-Object {
  $relative=$_.FullName.Substring($PSScriptRoot.Length).Replace('\','/')
  $overlay[$relative]=@{sha=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA1).Hash.ToLowerInvariant();local=$_.FullName}
}
if($overlay.Count -ne 8){throw 'Unexpected overlay inventory'}
$receiptPath=Join-Path $PSScriptRoot 'preview-private.json'
if($Mode -eq 'Prepare'){
  $original=Invoke-RestMethod "$api/sites/$siteId/files" -Headers $headers
  $files=@{};foreach($file in $original){$files[$file.path]=$file.sha}
  $baseline=(Get-FileHash (Join-Path $PSScriptRoot 'report-baseline.js') -Algorithm SHA1).Hash.ToLowerInvariant()
  if(Test-Path -LiteralPath $receiptPath){
    $prior=Get-Content -Raw $receiptPath | ConvertFrom-Json
    if($prior.deploy_id -eq $site.published_deploy.id){$baseline=$prior.files.PSObject.Properties['/scan/report.js'].Value}
  }
  if($files['/scan/report.js'] -ne $baseline){throw 'Production renderer changed; rebase before preparing'}
  foreach($asset in $overlay.Keys){$files[$asset]=$overlay[$asset].sha}
  $deploy=Invoke-RestMethod "$api/sites/$siteId/deploys?title=Management%20report%20design%20preview" -Headers $headers -Method Post -ContentType 'application/json' -Body (@{files=$files;draft=$true}|ConvertTo-Json -Depth 6 -Compress)
  foreach($required in $deploy.required){
    $asset=@($overlay.Keys|Where-Object{$overlay[$_].sha -eq $required})
    if($asset.Count -eq 0){throw 'Unexpected upload requested'}
    $assetPath=$asset[0]
    Invoke-RestMethod "$api/deploys/$($deploy.id)/files$assetPath" -Headers $headers -Method Put -ContentType 'application/octet-stream' -InFile $overlay[$assetPath].local | Out-Null
  }
  for($attempt=0;$attempt -lt 25;$attempt++){
    $ready=Invoke-RestMethod "$api/deploys/$($deploy.id)" -Headers $headers
    if($ready.state -eq 'ready'){break};if($ready.state -eq 'error'){throw 'Preview failed'};Start-Sleep -Seconds 2
  }
  if($ready.state -ne 'ready'){throw 'Preview not ready'}
  foreach($asset in $overlay.Keys){
    $bytes=(Invoke-WebRequest "$($ready.deploy_ssl_url)$asset" -UseBasicParsing).RawContentStream.ToArray()
    $digest=[Security.Cryptography.SHA1]::Create()
    $actual=([BitConverter]::ToString($digest.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    if($actual -ne $overlay[$asset].sha){throw 'Preview asset mismatch'}
  }
  # BEAAA-39654: no report page is pre-rendered (Founder, 11 Oct 2026: the report stays private); the non-JS first response must not be the loading placeholder.
  $firstResponse=(Invoke-WebRequest "$($ready.deploy_ssl_url)/scan/r/00000000000000000000000000000000" -UseBasicParsing).Content
  if($firstResponse.Contains('Loading your report')){throw 'Preview first response still carries the loading placeholder'}
  $rows=@($original|ForEach-Object{@{path=$_.path;sha=$_.sha}})
  @{deploy_id=$deploy.id;baseline_id=$site.published_deploy.id;preview_url=$ready.deploy_ssl_url;original=$rows;files=$files;overlay=$overlay;production_unchanged=$true} | ConvertTo-Json -Depth 10 | Set-Content $receiptPath
  @{preview_ready=$true;deploy_id=$deploy.id;preview_url=$ready.deploy_ssl_url;changed_assets=$overlay.Count;production_unchanged=$true} | ConvertTo-Json
}else{
  # Run only after Ari approves the final wording/design.
  $receipt=Get-Content -Raw $receiptPath|ConvertFrom-Json
  foreach($asset in $overlay.Keys){if($receipt.overlay.PSObject.Properties[$asset].Value.sha -ne $overlay[$asset].sha){throw 'Candidate changed after preview'}}
  if($site.published_deploy.id -ne $receipt.baseline_id){throw 'Concurrent production change; rebase'}
  $current=Invoke-RestMethod "$api/sites/$siteId/files" -Headers $headers
  $originalRows=@($receipt.original)
  if(@($originalRows|Where-Object {-not $_.path -or -not $_.sha}).Count){throw 'Invalid saved production inventory'}
  if($originalRows.Count -ne @($current).Count){throw 'Production inventory count changed'}
  if(Compare-Object @($originalRows|ForEach-Object{$_.path+':'+$_.sha}|Sort-Object) @($current|ForEach-Object{$_.path+':'+$_.sha}|Sort-Object)){throw 'Production inventory changed'}
  Invoke-RestMethod "$api/sites/$siteId/deploys/$($receipt.deploy_id)/restore" -Headers $headers -Method Post | Out-Null
  $published=Invoke-RestMethod "$api/sites/$siteId" -Headers $headers
  if($published.published_deploy.id -ne $receipt.deploy_id){throw 'Publication not confirmed'}
  $final=Invoke-RestMethod "$api/sites/$siteId/files" -Headers $headers
  if(@($final).Count -ne @($receipt.files.PSObject.Properties).Count){throw 'Published inventory count mismatch'}
  foreach($file in $final){if($receipt.files.PSObject.Properties[$file.path].Value -ne $file.sha){throw 'Published inventory mismatch'}}
  @{published=$true;deploy_id=$receipt.deploy_id;rollback_deploy_id=$receipt.baseline_id;published_at=$published.published_deploy.published_at;changed_assets=$overlay.Count;unrelated_files_preserved=$true} | ConvertTo-Json | Tee-Object -FilePath (Join-Path $PSScriptRoot 'production.json')
}
