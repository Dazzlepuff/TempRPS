<?php
declare(strict_types=1);

if (session_status() !== PHP_SESSION_ACTIVE) {
    ini_set('session.use_strict_mode', '1');
    session_set_cookie_params([
        'httponly' => true,
        'secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
        'samesite' => 'Lax',
    ]);
    session_start();
}

function escape_html(string $value): string {
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function csrf_token(): string {
    return $_SESSION['csrf_token'] ??= bin2hex(random_bytes(32));
}

function require_form_post(): void {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        header('Allow: POST');
        exit('Use the account form to submit this request.');
    }
    $token = $_POST['csrf_token'] ?? null;
    if (!is_string($token) || !hash_equals(csrf_token(), $token)) {
        http_response_code(403);
        exit('The form expired. Reload the page and try again.');
    }
}

function redirect_to(string $page): never {
    header('Location: ' . $page, true, 303);
    exit;
}

function account_database(): mysqli {
    $path = dirname(__DIR__) . '/config/accounts.local.json';
    $config = is_file($path) ? json_decode(file_get_contents($path), true, 512, JSON_THROW_ON_ERROR) : [];
    $password = getenv('RPS_DB_PASSWORD') ?: ($config['password'] ?? '');
    if ($password === '') {
        http_response_code(503);
        exit('Account database is not configured. See README.md.');
    }
    mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
    try {
        $connection = new mysqli(
            getenv('RPS_DB_HOST') ?: ($config['host'] ?? '127.0.0.1'),
            getenv('RPS_DB_USER') ?: ($config['username'] ?? 'mlrps_app'),
            $password,
            getenv('RPS_DB_NAME') ?: ($config['database'] ?? 'rock_paper_scissors'),
            (int) (getenv('RPS_DB_PORT') ?: ($config['port'] ?? 3307)),
        );
        $connection->set_charset('utf8mb4');
        return $connection;
    } catch (mysqli_sql_exception $error) {
        error_log('Account database connection failed (' . $error->getCode() . ')');
        http_response_code(503);
        exit('Account database is unavailable. Start it with scripts/start-accounts-db.ps1.');
    }
}
