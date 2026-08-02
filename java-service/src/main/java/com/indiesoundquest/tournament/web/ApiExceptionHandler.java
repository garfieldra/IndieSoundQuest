package com.indiesoundquest.tournament.web;
import java.util.*; import org.springframework.http.*; import org.springframework.web.bind.MethodArgumentNotValidException; import org.springframework.web.bind.annotation.*;
@RestControllerAdvice public class ApiExceptionHandler {
 @ExceptionHandler(NoSuchElementException.class) ResponseEntity<Map<String,Object>> notFound(){return error(HttpStatus.NOT_FOUND,"RESOURCE_NOT_FOUND","资源不存在或不可访问");}
 @ExceptionHandler(SecurityException.class) ResponseEntity<Map<String,Object>> forbidden(){return error(HttpStatus.FORBIDDEN,"TOURNAMENT_ACCESS_DENIED","赛事不可访问");}
 @ExceptionHandler({IllegalArgumentException.class,IllegalStateException.class}) ResponseEntity<Map<String,Object>> invalid(RuntimeException e){return error(HttpStatus.CONFLICT,"TOURNAMENT_STATE_INVALID",e.getMessage());}
 @ExceptionHandler(MethodArgumentNotValidException.class) ResponseEntity<Map<String,Object>> validation(){return error(HttpStatus.BAD_REQUEST,"VALIDATION_FAILED","请求参数不合法");}
 private ResponseEntity<Map<String,Object>> error(HttpStatus s,String code,String message){return ResponseEntity.status(s).body(Map.of("code",code,"message",message,"requestId",UUID.randomUUID().toString()));}
}
