[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$BaseUrl = "http://127.0.0.1:8000"
$Passed = 0
$Failed = 0
$PolicyEnvelope = $null
$ModelsEnvelope = $null

function Write-Result {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )

    if ($Ok) {
        $script:Passed += 1
        Write-Host ("PASS {0} - {1}" -f $Name, $Detail) -ForegroundColor Green
        return
    }

    $script:Failed += 1
    Write-Host ("FAIL {0} - {1}" -f $Name, $Detail) -ForegroundColor Red
}

function ConvertFrom-NdjsonText {
    param(
        [string]$Text
    )

    $events = @()
    foreach ($line in ($Text -split "\r?\n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }
        $events += ($trimmed | ConvertFrom-Json)
    }
    return $events
}

function Invoke-JsonGet {
    param(
        [string]$Path
    )

    return Invoke-RestMethod -Method Get -Uri ($BaseUrl + $Path) -TimeoutSec 30
}

function Invoke-JsonPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 100
    return Invoke-RestMethod -Method Post -Uri ($BaseUrl + $Path) -ContentType "application/json" -Body $json -TimeoutSec 60
}

function Invoke-NdjsonPost {
    param(
        [string]$Path,
        [hashtable]$Body,
        [int]$TimeoutSeconds = 60
    )

    $json = $Body | ConvertTo-Json -Depth 100
    $uri = $BaseUrl + $Path
    $request = [System.Net.WebRequest]::Create($uri)
    $request.Method = "POST"
    $request.ContentType = "application/json"
    $request.Accept = "application/x-ndjson, application/json"
    $request.Timeout = $TimeoutSeconds * 1000
    if ($request -is [System.Net.HttpWebRequest]) {
        $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    }

    $payloadBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $request.ContentLength = $payloadBytes.Length
    $requestStream = $request.GetRequestStream()
    try {
        $requestStream.Write($payloadBytes, 0, $payloadBytes.Length)
    }
    finally {
        $requestStream.Dispose()
    }

    $response = $null
    try {
        $response = $request.GetResponse()
    }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $response = $_.Exception.Response
        }
        else {
            throw
        }
    }

    try {
        $responseStream = $response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($responseStream, [System.Text.Encoding]::UTF8)
        try {
            $content = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
        if ($response -is [System.Net.HttpWebResponse]) {
            $statusCode = [int]$response.StatusCode
            if ($statusCode -lt 200 -or $statusCode -ge 300) {
                throw ("HTTP {0} from {1}: {2}" -f $statusCode, $uri, $content)
            }
        }
    }
    finally {
        if ($response) {
            $response.Dispose()
        }
    }

    return ConvertFrom-NdjsonText -Text $content
}

function Get-ModelId {
    param(
        $ModelItem
    )

    if ($null -eq $ModelItem) {
        return ""
    }
    if ($ModelItem.PSObject.Properties.Name -contains "model_id") {
        return [string]$ModelItem.model_id
    }
    if ($ModelItem.PSObject.Properties.Name -contains "id") {
        return [string]$ModelItem.id
    }
    if ($ModelItem.PSObject.Properties.Name -contains "name") {
        return [string]$ModelItem.name
    }
    return [string]$ModelItem
}

function Invoke-SmokeCheck {
    param(
        [string]$Name,
        [scriptblock]$Check
    )

    try {
        $detail = & $Check
        Write-Result -Name $Name -Ok $true -Detail ([string]$detail)
    }
    catch {
        $message = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $message = $_.ErrorDetails.Message
        }
        Write-Result -Name $Name -Ok $false -Detail $message
    }
}

Write-Host ("ComfyUIhybrid planner smoke test against {0}" -f $BaseUrl) -ForegroundColor Cyan
Write-Host ""

Invoke-SmokeCheck -Name "GET /health" -Check {
    $health = Invoke-JsonGet -Path "/health"
    if ($null -eq $health) {
        throw "Empty health payload."
    }

    if ($health.PSObject.Properties.Name -contains "ok" -and -not [bool]$health.ok) {
        throw ("Health response reported ok=false: {0}" -f ($health | ConvertTo-Json -Depth 20 -Compress))
    }

    if ($health.PSObject.Properties.Name -contains "status") {
        return ("status={0}" -f [string]$health.status)
    }

    return ($health | ConvertTo-Json -Depth 20 -Compress)
}

Invoke-SmokeCheck -Name "GET /planner/models" -Check {
    $script:ModelsEnvelope = Invoke-JsonGet -Path "/planner/models"
    $models = @($script:ModelsEnvelope.models)
    if ($models.Count -lt 1) {
        throw "No planner models were returned."
    }

    $names = @()
    foreach ($model in $models) {
        $names += (Get-ModelId -ModelItem $model)
    }
    return ("count={0}; first={1}" -f $models.Count, $names[0])
}

Invoke-SmokeCheck -Name "GET /planner/policy" -Check {
    $script:PolicyEnvelope = Invoke-JsonGet -Path "/planner/policy"
    if ($null -eq $script:PolicyEnvelope.policy) {
        throw "Planner policy response did not include a policy object."
    }

    $policy = $script:PolicyEnvelope.policy
    $mode = if ($policy.PSObject.Properties.Name -contains "mode") { [string]$policy.mode } else { "unknown" }
    $ladder = if ($policy.PSObject.Properties.Name -contains "auto_best_ladder") { [string]$policy.auto_best_ladder } else { "" }
    if ($ladder) {
        return ("mode={0}; auto_best_ladder={1}" -f $mode, $ladder)
    }
    return ("mode={0}" -f $mode)
}

Invoke-SmokeCheck -Name "POST /planner/policy" -Check {
    if ($null -eq $script:PolicyEnvelope) {
        $script:PolicyEnvelope = Invoke-JsonGet -Path "/planner/policy"
    }

    $policy = $script:PolicyEnvelope.policy
    if ($null -eq $policy) {
        throw "No planner policy is available to round-trip."
    }

    $body = @{}
    foreach ($key in @("mode", "manual_model_id", "ladder", "thresholds", "research_defaults", "auto_best_ladder")) {
        if ($policy.PSObject.Properties.Name -contains $key -and $null -ne $policy.$key) {
            $body[$key] = $policy.$key
        }
    }

    if ($body.Count -eq 0) {
        throw "Planner policy did not expose any writable fields."
    }

    $updated = Invoke-JsonPost -Path "/planner/policy" -Body $body
    if ($null -eq $updated.policy) {
        throw "Planner policy update response did not include a policy object."
    }

    $mode = if ($updated.policy.PSObject.Properties.Name -contains "mode") { [string]$updated.policy.mode } else { "unknown" }
    return ("mode={0}" -f $mode)
}

Invoke-SmokeCheck -Name "POST /planner/research/run" -Check {
    $fallback = "fallback_policy"

    $events = Invoke-NdjsonPost -Path "/planner/research/run" -TimeoutSeconds 120 -Body @{
        passes = 1
        per_pass_timeout_seconds = 1
        timeout_fallback = $fallback
    }

    if ($events.Count -lt 1) {
        throw "Research endpoint returned no events."
    }

    $started = @($events | Where-Object { $_.type -eq "research_started" })
    $completed = @($events | Where-Object { $_.type -eq "research_complete" })
    if ($started.Count -lt 1) {
        throw "Research stream did not include research_started."
    }
    if ($completed.Count -lt 1) {
        throw "Research stream did not include research_complete."
    }

    $bestLadder = ""
    if ($completed[0].data -and $completed[0].data.PSObject.Properties.Name -contains "best_ladder") {
        $bestLadder = [string]$completed[0].data.best_ladder
    }
    if ($bestLadder) {
        return ("events={0}; best_ladder={1}" -f $events.Count, $bestLadder)
    }
    return ("events={0}" -f $events.Count)
}

Invoke-SmokeCheck -Name "POST /helper/process" -Check {
    $events = Invoke-NdjsonPost -Path "/helper/process" -Body @{
        query = "tool:list_dir {`"path`":`".`"}"
        settings = @{
            tool_policy = "required"
        }
    }

    if ($events.Count -lt 1) {
        throw "Helper process returned no events."
    }

    $toolCall = @($events | Where-Object { $_.type -eq "tool_call" })
    $toolResult = @($events | Where-Object { $_.type -eq "tool_result" })
    $done = @($events | Where-Object { $_.type -eq "done" })
    if ($toolCall.Count -lt 1) {
        throw "Helper stream did not include tool_call."
    }
    if ($toolResult.Count -lt 1) {
        throw "Helper stream did not include tool_result."
    }
    if ($done.Count -lt 1) {
        throw "Helper stream did not include done."
    }

    return ("events={0}; tool={1}" -f $events.Count, [string]$toolCall[0].data.name)
}

Write-Host ""
Write-Host "Summary" -ForegroundColor Cyan
Write-Host ("PASS={0} FAIL={1}" -f $Passed, $Failed)

if ($Failed -gt 0) {
    exit 1
}

exit 0
