[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Send", "Receive", "Wait")]
    [string]$Action,

    [string]$BridgeUrl = "http://192.168.50.1:58081",
    [string]$Token,

    [ValidateSet("ubuntu", "windows")]
    [string]$Sender = "windows",
    [ValidateSet("ubuntu", "windows")]
    [string]$Recipient = "windows",
    [ValidateSet("task", "result", "status", "note")]
    [string]$Kind = "note",
    [string]$Message,
    [string]$CorrelationId,
    [int]$After = 0,
    [int]$WaitSeconds = 30
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Token)) {
    $Token = $env:AGENT_BRIDGE_TOKEN
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    $tokenPath = Join-Path $PSScriptRoot "token.txt"
    if (Test-Path -LiteralPath $tokenPath) {
        $Token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "No token supplied. Use -Token, AGENT_BRIDGE_TOKEN, or $PSScriptRoot\token.txt"
}

$headers = @{ Authorization = "Bearer $Token" }

function Receive-Messages {
    param([int]$Cursor)
    $uri = "$BridgeUrl/v1/messages?recipient=$Recipient&after=$Cursor"
    (Invoke-RestMethod -Method Get -Uri $uri -Headers $headers).messages
}

switch ($Action) {
    "Send" {
        if ([string]::IsNullOrWhiteSpace($Message)) {
            throw "-Message is required for Send"
        }
        if ($Sender -eq $Recipient) {
            throw "Sender and Recipient must differ"
        }
        $body = @{
            sender         = $Sender
            recipient      = $Recipient
            kind           = $Kind
            correlation_id = $CorrelationId
            message        = $Message
        } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri "$BridgeUrl/v1/messages" `
            -Headers $headers -ContentType "application/json" -Body $body
    }
    "Receive" {
        Receive-Messages -Cursor $After
    }
    "Wait" {
        $deadline = (Get-Date).AddSeconds($WaitSeconds)
        do {
            $messages = @(Receive-Messages -Cursor $After)
            if ($messages.Count -gt 0) {
                $messages
                break
            }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline)
    }
}
