<?php
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if (!in_array($path, ['/', '/index.php'], true)) {
    http_response_code(404);
    exit('Not found');
}
header('Location: /user_login_menu.php', true, 302);
exit;
