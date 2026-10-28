<?php
/**
 * Plugin Name: Afrand Wallet Bridge
 * Description: Phone-based customer registration and idempotent TeraWallet credits for the Afrand panel.
 * Version: 1.1.0
 * Requires PHP: 7.4
 */
defined('ABSPATH') || exit;

final class Afrand_Wallet_Bridge {
    const VERSION = '1.1.0';
    const NS = 'wc/v3';
    static function table() { global $wpdb; return $wpdb->prefix . 'afrand_wallet_operations'; }
    static function activate() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $table = self::table();
        dbDelta("CREATE TABLE $table (
            operation_key varchar(64) NOT NULL,
            fingerprint varchar(64) NOT NULL,
            state varchar(20) NOT NULL,
            result_json longtext NULL,
            created_at datetime NOT NULL,
            updated_at datetime NOT NULL,
            PRIMARY KEY  (operation_key)
        ) " . $wpdb->get_charset_collate() . ';');
    }
    static function permission() {
        if (!current_user_can('manage_woocommerce')) return new WP_Error('afrand_forbidden', 'دسترسی مدیریت کیف پول لازم است.', array('status'=>403));
        if (!function_exists('woo_wallet') || !function_exists('get_woocommerce_currency')) return new WP_Error('afrand_unavailable', 'کیف پول فعال نیست.', array('status'=>503));
        return true;
    }
    static function routes() {
        foreach (array(
            '/status'=>array('GET','status'),
            '/customers'=>array('GET','search'),
            '/customers/create'=>array('POST','create_customer'),
            '/customers/lookup'=>array('POST','lookup_customers'),
            '/customers/(?P<id>\d+)'=>array('GET','customer'),
            '/customers/(?P<id>\d+)/company'=>array('PUT','update_company'),
            '/credits'=>array('POST','credit'),
            '/operations/(?P<key>[a-f0-9]{64})'=>array('GET','operation'),
        ) as $path=>$handler) {
            register_rest_route(self::NS, '/afrand-wallet'.$path, array('methods'=>$handler[0], 'callback'=>array(__CLASS__,$handler[1]), 'permission_callback'=>array(__CLASS__,'permission')));
        }
    }
    static function error($message, $status=400, $code='afrand_validation') { return new WP_Error($code, $message, array('status'=>$status)); }
    static function phone($value) {
        $value = strtr((string)$value,array_combine(preg_split('//u','۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩',-1,PREG_SPLIT_NO_EMPTY),str_split('01234567890123456789')));
        $value = preg_replace('/\D/','',$value);
        if (strlen($value)===14 && substr($value,0,4)==='0098') $value='0'.substr($value,4);
        elseif (strlen($value)===12 && substr($value,0,2)==='98') $value='0'.substr($value,2);
        elseif (strlen($value)===10 && substr($value,0,1)==='9') $value='0'.$value;
        return $value;
    }
    static function user_phone($id) {
        foreach (array('afrand_profile_phone','billing_phone','phone','digits_phone_no') as $key) {
            $value=get_user_meta($id,$key,true);
            if ($value) return self::phone($value);
        }
        $user=get_userdata($id);
        return $user && preg_match('/^(?:0?9|\+?98|0098)/',$user->user_login) ? self::phone($user->user_login) : '';
    }
    static function public_user($user) {
        return array('id'=>(int)$user->ID,'first_name'=>(string)get_user_meta($user->ID,'first_name',true),'last_name'=>(string)get_user_meta($user->ID,'last_name',true),'name'=>$user->display_name,'phone'=>self::user_phone($user->ID),'company'=>(string)get_user_meta($user->ID,'billing_company',true));
    }
    static function phone_matches($phone) {
        // Exact normalization includes legacy Persian digits and +98 billing phones.
        global $wpdb;
        $ids=$wpdb->get_col("SELECT DISTINCT user_id FROM {$wpdb->usermeta} WHERE meta_key IN ('afrand_profile_phone','billing_phone','phone','digits_phone_no')");
        $ids=array_merge($ids,$wpdb->get_col($wpdb->prepare("SELECT ID FROM {$wpdb->users} WHERE user_login IN (%s,%s,%s,%s)",$phone,substr($phone,1),'98'.substr($phone,1),'+98'.substr($phone,1))));
        $matches=array();
        foreach(array_unique($ids) as $id) if (self::user_phone($id)===$phone && get_userdata($id)) $matches[]=get_userdata($id);
        return $matches;
    }
    static function lookup_customers($r) {
        global $wpdb;
        $phones=$r->get_param('phones');
        if (!is_array($phones) || count($phones)>500) return self::error('فهرست موبایل معتبر نیست.');
        $result=array();
        foreach ($phones as $phone) {
            $phone=self::phone($phone);
            if (!preg_match('/^09\d{9}$/',$phone)) return self::error('موبایل معتبر نیست.');
            $result[$phone]=array();
        }
        $ids=$wpdb->get_col("SELECT DISTINCT user_id FROM {$wpdb->usermeta} WHERE meta_key IN ('afrand_profile_phone','billing_phone','phone','digits_phone_no')");
        if ($result) {
            $logins=array(); foreach(array_keys($result) as $phone) { $logins[]=$phone; $logins[]=substr($phone,1); $logins[]='98'.substr($phone,1); $logins[]='+98'.substr($phone,1); }
            $placeholders=implode(',',array_fill(0,count($logins),'%s'));
            $ids=array_merge($ids,$wpdb->get_col($wpdb->prepare("SELECT ID FROM {$wpdb->users} WHERE user_login IN ($placeholders)",$logins)));
        }
        foreach (array_unique($ids) as $id) { $phone=self::user_phone($id); $user=get_userdata($id); if ($user && array_key_exists($phone,$result)) $result[$phone][]=self::public_user($user); }
        return array('matches'=>$result);
    }
    static function status($r) {
        if (!self::supports_wallet_locks()) return self::error('Database version does not support safe wallet locks.',503,'afrand_database_version');
        return array('connected'=>true,'wallet'=>'TeraWallet','bridge_version'=>self::VERSION,'currency'=>get_woocommerce_currency(),'decimals'=>wc_get_price_decimals());
    }
    static function search($r) {
        $q=trim(sanitize_text_field((string)$r->get_param('q')));
        $page=max(1,min(100,(int)$r->get_param('page')));
        if (mb_strlen($q)<2) return self::error('حداقل دو نویسه برای جست‌وجو وارد کنید.');
        $phone=self::phone($q);
        if (preg_match('/^09\d{9}$/',$phone)) return array('customers'=>array_map(array(__CLASS__,'public_user'),self::phone_matches($phone)),'page'=>1,'has_more'=>false);
        $query=new WP_User_Query(array('search'=>'*'.$q.'*','search_columns'=>array('user_login','display_name'),'number'=>21,'offset'=>($page-1)*20,'orderby'=>'ID','order'=>'DESC','count_total'=>false));
        $users=$query->get_results();
        return array('customers'=>array_map(array(__CLASS__,'public_user'),array_slice($users,0,20)),'page'=>$page,'has_more'=>count($users)>20);
    }
    static function wallet_data($id) {
        global $wpdb;
        $currency=get_woocommerce_currency();
        $balance=woo_wallet()->wallet->get_wallet_balance($id,'edit',$currency);
        $table=$wpdb->base_prefix.'woo_wallet_transactions';
        $rows=$wpdb->get_results($wpdb->prepare("SELECT transaction_id,type,amount,currency,details,date FROM $table WHERE user_id=%d AND deleted=0 ORDER BY transaction_id DESC LIMIT 30",$id),ARRAY_A);
        if ($wpdb->last_error) return array('balance'=>(string)$balance,'currency'=>$currency,'transactions'=>array(),'history_error'=>true);
        return array('balance'=>(string)$balance,'currency'=>$currency,'transactions'=>$rows ?: array());
    }
    static function customer($r) {
        $user=get_userdata((int)$r['id']);
        if (!$user) return self::error('مشتری پیدا نشد.',404);
        return array('customer'=>self::public_user($user),'wallet'=>self::wallet_data($user->ID));
    }
    static function update_company($r) {
        $id=(int)$r['id'];
        if (!current_user_can('edit_user',$id)) return self::error('دسترسی ویرایش این مشتری لازم است.',403);
        if (!get_userdata($id)) return self::error('مشتری پیدا نشد.',404);
        $value=$r->get_param('company'); $expected=$r->get_param('expected_company');
        if ($r->get_param('confirm')!==true || !is_string($value) || !is_string($expected) || mb_strlen($value)>120 || mb_strlen($expected)>120) return self::error('شرکت و مقدار قبلی و تأیید لازم است.');
        $value=trim(sanitize_text_field($value));
        $lock='company-'.$id;
        if (!self::lock($lock)) return self::error('ویرایش شرکت در حال انجام است.',409);
        try {
            // Never overwrite a concurrent edit, nor silently erase on customer replay/import.
            clean_user_cache($id);
            if ((string)get_user_meta($id,'billing_company',true)!==$expected) return self::error('شرکت تغییر کرده است؛ مشتری را دوباره انتخاب کنید.',409,'afrand_conflict');
            $customer=new WC_Customer($id);
            $customer->set_billing_company($value); $customer->save();
            clean_user_cache($id);
            if ((string)get_user_meta($id,'billing_company',true)!==$value) return self::error('ذخیره شرکت تأیید نشد؛ مقدار را دوباره بخوانید.',503);
            return array('customer'=>self::public_user(get_userdata($id)));
        } finally { self::unlock($lock); }
    }
    static function supports_wallet_locks() {
        global $wpdb;
        $version=(string)$wpdb->get_var('SELECT VERSION()');
        if (stripos($version,'MariaDB')!==false) {
            return preg_match('/(\d+\.\d+\.\d+)-MariaDB/i',$version,$match) && version_compare($match[1],'10.0.2','>=');
        }
        return preg_match('/^(\d+\.\d+\.\d+)/',$version,$match) && version_compare($match[1],'5.7.5','>=');
    }
    static function lock($key) {
        global $wpdb;
        return (int)$wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s, 5)','afrand_'.substr(hash('sha256',$key),0,48)))===1;
    }
    static function unlock($key) { global $wpdb; $wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)','afrand_'.substr(hash('sha256',$key),0,48))); }
    static function create_customer($r) {
        global $wpdb;
        if (!current_user_can('create_users')) return self::error('دسترسی ساخت مشتری لازم است.',403);
        if ($r->get_param('confirm')!==true) return self::error('تأیید ثبت مشتری لازم است.');
        $phone=self::phone($r->get_param('phone'));
        $first=sanitize_text_field((string)$r->get_param('first_name')); $last=sanitize_text_field((string)$r->get_param('last_name'));
        $company=trim(sanitize_text_field((string)$r->get_param('company')));
        if (mb_strlen($company)>120) return self::error('شرکت حداکثر ۱۲۰ نویسه است.');
        if (!preg_match('/^09\d{9}$/',$phone) || !$first || mb_strlen($first)>80 || mb_strlen($last)>80) return self::error('نام و موبایل معتبر لازم است.');
        $lock='phone-'.$phone;
        if (!self::lock($lock)) return self::error('ثبت مشتری در حال انجام است؛ دوباره بررسی کنید.',409);
        try {
            $matches=self::phone_matches($phone);
            if (count($matches)>1) return self::error('چند کاربر با این موبایل وجود دارد؛ مشتری را از نتایج انتخاب کنید.',409);
            if ($matches) return array('customer'=>self::public_user($matches[0]),'replayed'=>true);
            if (username_exists($phone)) return self::error('این نام کاربری به حساب دیگری تعلق دارد.',409);
            $id=wp_insert_user(array('user_login'=>$phone,'user_pass'=>wp_generate_password(32,true,true),'user_email'=>'','first_name'=>$first,'last_name'=>$last,'display_name'=>trim($first.' '.$last),'role'=>'customer'));
            if (is_wp_error($id)) return self::error('ساخت مشتری در وردپرس انجام نشد.',409);
            update_user_meta($id,'billing_phone',$phone); update_user_meta($id,'afrand_profile_phone',$phone);
            update_user_meta($id,'billing_first_name',$first); update_user_meta($id,'billing_last_name',$last);
            update_user_meta($id,'afrand_created_by','wallet-panel');
            $customer=new WC_Customer($id); $customer->set_billing_company($company); $customer->save();
            return array('customer'=>self::public_user(get_userdata($id)),'replayed'=>false);
        } finally { self::unlock($lock); }
    }
    static function saved($key) {
        global $wpdb;
        return $wpdb->get_row($wpdb->prepare('SELECT * FROM '.self::table().' WHERE operation_key=%s',$key),ARRAY_A);
    }
    static function reconcile($key,$row) {
        global $wpdb;
        if ($row['state']==='succeeded') { $result=json_decode($row['result_json'],true); $result['replayed']=true; return $result; }
        $payload=json_decode($row['result_json'],true);
        $table=$wpdb->base_prefix.'woo_wallet_transactions';
        $transactions=$wpdb->get_results($wpdb->prepare("SELECT transaction_id,user_id,amount,currency,type FROM $table WHERE details LIKE %s AND deleted=0",'%'.$wpdb->esc_like('[AFRAND:'.$key.']').'%'),ARRAY_A);
        if ($wpdb->last_error || !is_array($transactions) || count($transactions)!==1) return self::error('نتیجهٔ واریز هنوز مشخص نیست. مبلغ دوباره واریز نمی‌شود؛ در تراکنش‌های وردپرس بررسی کنید.',409,'afrand_unknown');
        $t=$transactions[0];
        if ((int)$t['user_id']!==$payload['customer_id'] || $t['type']!=='credit' || (float)$t['amount']!==(float)$payload['amount'] || $t['currency']!==$payload['currency']) return self::error('تراکنش نیازمند بررسی است.',409,'afrand_unknown');
        $result=array_merge($payload,array('transaction_id'=>(int)$t['transaction_id'],'replayed'=>true,'state'=>'succeeded'));
        $wpdb->update(self::table(),array('state'=>'succeeded','result_json'=>wp_json_encode($result),'updated_at'=>current_time('mysql',true)),array('operation_key'=>$key));
        return $result;
    }
    static function operation($r) {
        $key=$r['key'];
        if (!self::lock('credit-'.$key)) return self::error('واریز در حال بررسی است.',409,'afrand_unknown');
        try {
            $row=self::saved($key);
            if (!$row) return self::error('این درخواست هنوز ثبت نشده است.',404,'afrand_not_found');
            $result=self::reconcile($key,$row);
            if (is_wp_error($result)) return $result;
            // Use the same WordPress sanitizer as credit/fingerprint, including for
            // operations saved before this verification field was introduced.
            // Keep the persisted note/fingerprint unchanged; verify, never rewrite.
            $expected=$r->get_param('expected_note');
            if ($expected!==null) {
                if (!is_string($expected) || !isset($result['note']) || sanitize_text_field($expected)!==$result['note']) return self::error('عنوان تراکنش با سند ثبت‌شده یکسان نیست.',409,'afrand_conflict');
                $result['verified_note']=$expected;
            }
            return $result;
        }
        finally { self::unlock('credit-'.$key); }
    }
    static function credit($r) {
        global $wpdb;
        // Older servers release the first named lock when taking the second one.
        if (!self::supports_wallet_locks()) return self::error('Database version does not support safe wallet locks.',503,'afrand_database_version');
        $key=(string)$r->get_param('request_id'); $id=(int)$r->get_param('customer_id');
        $amount=(string)$r->get_param('amount'); $currency=(string)$r->get_param('currency');
        $note=sanitize_text_field((string)$r->get_param('note'));
        if ($r->get_param('confirm')!==true || !preg_match('/^[a-f0-9]{64}$/',$key) || !get_userdata($id) || !preg_match('/^[0-9]+(?:\.[0-9]{1,2})?$/',$amount) || (float)$amount<=0 || (float)$amount>100000000 || $currency!==get_woocommerce_currency() || mb_strlen($note)>240) return self::error('اطلاعات واریز معتبر نیست.');
        // Refuse rounding or currency conversion of the operator's intended amount.
        if ((float)wc_format_decimal($amount,wc_get_price_decimals())!==(float)$amount) return self::error('دقت مبلغ با واحد پول کیف پول هماهنگ نیست.');
        $payload=array('customer_id'=>$id,'amount'=>wc_format_decimal($amount,wc_get_price_decimals()),'currency'=>$currency,'note'=>$note);
        $fingerprint=hash('sha256',wp_json_encode($payload));
        if (!self::lock('credit-'.$key)) return self::error('این واریز در حال انجام است.',409,'afrand_unknown');
        try {
            $row=self::saved($key);
            if ($row) {
                if ($row['fingerprint']!==$fingerprint) return self::error('شناسهٔ واریز قبلاً با اطلاعات دیگری استفاده شده است.',409,'afrand_conflict');
                return self::reconcile($key,$row);
            }
            // Different invoices for one wallet must not race its balance update.
            // Requires MySQL >=5.7.5 / MariaDB with multiple named locks per connection.
            $wallet_lock='wallet-'.$id;
            if (!self::lock($wallet_lock)) return self::error('کیف پول در حال پردازش سند دیگری است؛ دوباره بررسی کنید.',503);
            try {
            // A persistent object cache may contain a balance from before acquiring the lock.
            clean_user_cache($id);
            $now=current_time('mysql',true);
            if (!$wpdb->insert(self::table(),array('operation_key'=>$key,'fingerprint'=>$fingerprint,'state'=>'processing','result_json'=>wp_json_encode($payload),'created_at'=>$now,'updated_at'=>$now))) return self::error('ثبت شناسهٔ واریز انجام نشد.',503);
            $details='واریز از پنل افرند'.($note ? ' — '.$note : '').' [AFRAND:'.$key.']';
            $transaction=woo_wallet()->wallet->credit($id,(float)$amount,$details,array('currency'=>$currency));
            if (!$transaction || is_wp_error($transaction)) return self::error('پاسخ واریز نامشخص است؛ نتیجه را بررسی کنید.',409,'afrand_unknown');
            // Independently read the native wallet ledger before reporting success.
            $result=self::reconcile($key,self::saved($key));
            if (!is_wp_error($result)) $result['replayed']=false;
            return $result;
            } finally { self::unlock($wallet_lock); }
        } finally { self::unlock('credit-'.$key); }
    }
}
register_activation_hook(__FILE__,array('Afrand_Wallet_Bridge','activate'));
add_action('rest_api_init',array('Afrand_Wallet_Bridge','routes'));
